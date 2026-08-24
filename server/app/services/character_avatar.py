"""角色头像：手动上传与按需 AI 生成。

与 NPC 立绘走同一条路（文本模型写提示词 → 生图模型出图 → image_store 落盘 → 回写 URL），
所以两者产出的图在系统里毫无区别；差别只在素材来源——玩家角色有建卡时那六段结构化背景
（个人描述/思想信念/特点/重要之人/意义非凡之地/宝贵之物），比 NPC 的一句 description
丰富得多，值得单独喂进提示词。

**生成是手动触发的**：建卡途中角色描述往往还没定，那时出的图既费钱又不像；等玩家自己
觉得这张卡成型了再点，命中率高得多。
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.ai.image_gen import get_image_llm
from app.ai.llm_factory import get_fast_llm
from app.models.character import Character
from app.models.module import Module
from app.services import image_store, module_image_service

logger = logging.getLogger(__name__)

_AVATAR_PROMPT_SYS = (
    "你是文生图提示词工程师。把给定的 TRPG 玩家角色转成一行**英文** Stable Diffusion "
    "提示词：该人物的半身肖像（character portrait, bust shot，按给定年代取服饰）。"
    "据外貌/职业/性格/信念描绘气质与神态，让人一眼看出这是谁。"
    "画风词不用写，系统会统一追加。不要出现真实人名，不要引号，只输出提示词本身。"
    + module_image_service.PORTRAIT_IDENTITY_RULE
    + module_image_service.SAFETY_PROMPT_RULE
)

#: 喂进提示词的建卡背景段（键 → 中文标签），与 ai_character_service.BACKSTORY_LABELS 同源。
#: 只取「看得见的部分」：外貌与气质相关的几段。重要之人/意义非凡之地不进画面——
#: 它们描述的是别人和别处，塞进肖像提示词只会把画面带偏。
_VISUAL_SECTIONS = (
    ("personalDescription", "个人描述"),
    ("traits", "特点"),
    ("ideologyBeliefs", "思想信念"),
)


def _prompt_user(char: Character, module: Module | None) -> str:
    sd = char.system_data or {}
    era = str((getattr(module, "world_setting", None) or {}).get("era") or "1920s")
    parts = [
        f"年代：{era}",
        f"姓名：{char.name}",
    ]
    # 性别是卡上写死的事实，却一直没往下递——模型只能按名字猜，「加布里埃尔」这类
    # 译名在中文里看不出性别，猜错就画错人（NPC 立绘同款问题）。
    gender = str(sd.get("gender") or "").strip()
    if gender:
        parts.append(f"性别：{gender}")
    occupation = str(sd.get("occupation") or "").strip()
    if occupation:
        parts.append(f"职业：{occupation}")
    age = sd.get("age")
    if age:
        parts.append(f"年龄：{age}")
    for key, label in _VISUAL_SECTIONS:
        text = str(sd.get(key) or "").strip()
        if text:
            parts.append(f"{label}：{text[:200]}")
    backstory = (char.backstory or "").strip()
    if backstory and not any(sd.get(k) for k, _ in _VISUAL_SECTIONS):
        # 六段结构化背景缺失时（导入的卡、早期建的卡）才回落整段 backstory
        parts.append(f"背景：{backstory[:300]}")
    return "\n".join(parts)


async def generate_avatar(db: Session, char: Character) -> str | None:
    """给角色生成一张头像并写回 ``avatar_url``；任何环节失败返回 None。

    失败一律返回 None 而不抛：头像是增强能力，没有它角色卡照常可用（回落首字纹章）。
    调用方据 None 给出可读的错误文案。
    """
    image_llm = get_image_llm()
    if not image_llm.supports_image_gen():
        return None
    module = db.get(Module, char.module_id) if char.module_id else None
    # 写提示词与出图共用同一份画风（见 module_image_service.style_discipline）
    suffix = module_image_service.style_suffix_for(module)
    try:
        raw = await get_fast_llm().complete(
            [
                {"role": "system",
                 "content": _AVATAR_PROMPT_SYS + module_image_service.style_discipline(suffix)},
                {"role": "user", "content": _prompt_user(char, module)},
            ],
            temperature=0.7,
        )
        prompt = module_image_service.trim_prompt(raw)
        if not prompt:
            return None
        b64 = await image_llm.generate_image(f"{prompt}, {suffix}")
        if not b64:
            return None
        url = image_store.save_image_b64(b64)
        if not url:
            return None
        char.avatar_url = url
        db.commit()
        db.refresh(char)
        return url
    except Exception:  # noqa: BLE001 — 头像是增强能力，失败只回 None
        logger.exception("角色头像生成失败：character=%s", char.id)
        db.rollback()
        return None


def set_avatar(db: Session, char: Character, url: str | None) -> Character:
    """直接设置（或摘掉）头像 URL。摘掉后前端回落首字纹章，不是缺陷状态。"""
    char.avatar_url = url or None
    db.commit()
    db.refresh(char)
    return char
