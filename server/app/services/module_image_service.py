"""模组结构化条目的配图生成与缓存修复。"""

from __future__ import annotations

import asyncio
import logging
import re

from sqlalchemy.orm import Session

from app.ai.image_gen import get_image_llm
from app.ai.llm_factory import get_fast_llm
from app.models.module import Module
from app.services import image_store, style_presets

logger = logging.getLogger(__name__)

_IMAGE_NAME_RE = re.compile(r"^[a-f0-9]{32}\.(?:jpg|jpeg|png|webp)$")

# 画风后缀（确定性追加在快模型产出的提示词之后，保证同一局里风格一致）。
#
# 具体文案与「预设 / 自定义」的取值约定见 services.style_presets；本模块只负责
# 「从模组与会话上取出该用哪一档」，各调用方一律经 style_suffix_for() 拿，
# 不要再各自拼字符串——原来三处各写一份完全相同的常量，改一处就和另两处不一致。
#
# 不再强制黑白：`mostly black and white` 把每张图都压成灰的，场景之间失去区分度——
# 煤油灯的暖黄、地窖的霉绿、雪夜的冷蓝本该是各自最有辨识度的东西，也是场景氛围底赖以
# 成立的前提（见 index.css 的 --scene-backdrop-*：它拿配图当色调来源，源图没颜色就没色调）。
# 改成「低饱和的有限色调」：仍是阴郁墨线漫画质感，但允许每个场景有自己的主色倾向。
# 尾部那句 fully clothed 是内容红线的一部分，见 SAFETY_PROMPT_RULE。
#: 未选画风时的后缀（= style_presets 的默认档 + 内容红线），与本特性上线前逐字等价。
IMAGE_STYLE_SUFFIX = style_presets.image_style_suffix()
_STYLE_SUFFIX = IMAGE_STYLE_SUFFIX   # 兼容本模块内既有引用


def style_suffix_for(module=None, session=None) -> str:
    """本次出图该用的画风后缀：会话 > 模组 > 默认档，内容红线永远在最后。

    模组配图（场景/NPC 立绘）没有会话语境，只传 module；对局内插画两者都传。
    """
    return style_presets.image_style_suffix(
        getattr(session, "image_style", "") if session is not None else "",
        getattr(module, "default_image_style", "") if module is not None else "",
    )


#: 正文允许的最大词数。CLIP 一次只吃 77 token，画风后缀本身就占掉三十来词；正文再长，
#: 后缀就掉进第二个 chunk、权重骤降，模型回落到自己的写实倾向。实测同一句正文、同一个后缀，
#: 27 词出标准墨线漫画，63 词出写实照片——长度是那次对照里唯一的变量。
PROMPT_MAX_WORDS = 35


def trim_prompt(raw: str, max_words: int = PROMPT_MAX_WORDS) -> str:
    """把快模型写的提示词收进词数上限：取首行，按逗号累加到超限为止。

    砍在逗号处而不是词中间——SD 提示词是逗号分隔的短语列表，从中间截断会留下半个短语。
    首个短语哪怕自己就超限也保留（它是画面主体，砍了就什么都不剩）。

    必须在代码里硬截：提示词里写了词数要求，模型照做与否是概率问题，而超限一次就是
    一张风格跑掉的图。
    """
    text = (raw or "").strip()
    if not text:
        return ""
    text = text.splitlines()[0].strip()[:500]
    out: list[str] = []
    used = 0
    for part in text.split(","):
        chunk = part.strip()
        if not chunk:
            continue
        n = len(chunk.split())
        if out and used + n > max_words:
            break
        out.append(chunk)
        used += n
    return ", ".join(out)


def style_discipline(suffix: str) -> str:
    """拼进 *_PROMPT_SYS 的一段：告诉写提示词的模型，成图将是什么画风。

    **为什么写内容的模型必须知道画风。** 原先的分工是「快模型只管写内容，画风由系统在
    末尾统一追加」，而各 *_PROMPT_SYS 又明确要求它写「光影、天气与年代质感」。它照做了，
    于是写出 ``warm golden sunlight``、``vibrant market atmosphere``、``urban realism``
    ——正好和默认画风要的 ``muted low-saturation, gritty dark comic`` 顶着来。后缀还挂在
    四十来词正文的最末尾，权重被稀释，生图模型每次倒向哪一半近乎随机：《鬼屋》里同一天、
    同一份代码出的两张场景图，一张是高反差黑白照片、一张是纯白底线稿，连后缀自己要的
    网点与主色倾向都没吃到。

    光影本身还是要写的（它是画面信息），只是把**色调**的裁量权收回给画风那一段。

    **禁的是整类，不是几个词。** 头一版只列了 vibrant / colorful / saturated 这些，
    模型立刻换了个说法接着犯：它写 ``muted sepia tones``——低饱和确实照做了，可 sepia
    正是老照片的颜色，媒介当场跑偏，出来又是一张写实照片。它并不知道目标画风想要什么色调，
    所以任何由它自选的色调词都是在赌。列举永远追不上换词，只能整类收走。
    """
    return (
        "\n\n【画风纪律】本次成图会在你的提示词之后统一追加下面这段画风词，"
        "你写的内容**必须与它兼容**：\n"
        f"{suffix}\n"
        f"另外，整条提示词**不得超过 {PROMPT_MAX_WORDS} 个英文词**：画风词要和你的正文挤在"
        "同一个 CLIP 窗口里，正文一长，画风就被挤出去、成图直接跑回写实照片。"
        "挑最能立住这个画面的几样东西写，不要面面俱到。\n"
        "两条硬要求：\n"
        "1. 不写任何**媒介或渲染方式**（photo、photograph、realistic、realism、3d render、"
        "cinematic、hyperrealistic、film still 等）——媒介已经由上面那段画风词定死了。\n"
        "2. 不写任何**整体色调/ 调色**的描述。不只是 vibrant、colorful 这类，"
        "sepia、monochrome、warm tones、muted tones、golden hour、色温与胶片色一概不写，"
        "哪怕你觉得它符合上面的画风——色调完全由画风那一段决定，你写的每一个色调词都在跟它抢。\n"
        "光影照写，但只写**光源与明暗关系**：光从哪来（吊灯、窗、街灯）、照亮什么、"
        "投下什么阴影。物体本身固有的颜色（红砖、黑漆门）可以写，那是画面信息不是调色。"
    )

#: 写提示词的那个模型要守的内容红线，拼进每个 *_PROMPT_SYS。
#:
#: 三道闸各管一段，缺一不可：
#:   ① 这条 —— 管住提示词**源头**，模组里「衣着不整的受害者」「浴室」这类描述不会被原样
#:      翻译成露骨的英文提示词；
#:   ② IMAGE_STYLE_SUFFIX 末尾的 fully clothed / sfw —— 正向兜一道；
#:   ③ comfyui.SAFETY_NEGATIVE —— 负面词直接作用于采样，是最硬的一道，但**只有 ComfyUI 有**：
#:      OpenAI 兼容的生图接口根本没有负面提示词参数，那条路径全靠 ① 和 ②。
SAFETY_PROMPT_RULE = (
    "内容红线（高于一切其他要求）：产出的提示词**绝不能**包含裸露、性暗示、情色或未成年人相关"
    "的描写。原文若涉及衣着不整、性暴力或类似内容，一律改写成不露骨的表达（如以阴影、背影、"
    "凌乱的房间、地上的衣物暗示），人物一律穿着完整。"
)

SCENE_PROMPT_SYS = (
    "你是文生图提示词工程师。把给定的 TRPG 场景转成一行**英文** Stable Diffusion 提示词："
    "只描绘该地点的空镜画面内容——环境/建筑、光影、天气与年代质感，按给定年代取材"
    "（如 abandoned train car, flickering lights）。危险度越高画面越阴沉压抑。画风词不用写，系统会统一追加。"
    "不要出现人物面孔与真实人名，不要引号，只输出提示词本身。"
    # 章节类场景（「委托与准备」这种）说的是一段情节、不是一个地方，直接照「画这个地点」
    # 去写，模型只能自己现编：同一条描述三次取样编出了公寓走廊、白天街道、黄昏郊区街。
    # 描述里其实藏着地点（「接受房东的委托」→ 房东的会客处），指出来让它去找。
    "\n若给定的是一段情节而非具体地点（描述写的是「做了什么」），"
    "先从描述里推断这件事**发生在哪儿**，再画那个地方的空镜；推断不出就画一处符合"
    "年代与氛围的中性室内景，不要凭空另编一个与描述无关的地方，也不要画人物动作。"
    + SAFETY_PROMPT_RULE
)

NPC_PROMPT_SYS = (
    "你是文生图提示词工程师。把给定的 TRPG NPC 转成一行**英文** Stable Diffusion 提示词："
    "该人物的半身肖像（character portrait, bust shot，按给定年代取服饰）。据外貌/身份/性格"
    "描绘气质与神态。画风词不用写，系统会统一追加。不要出现真实人名，不要引号，只输出提示词本身。"
    + SAFETY_PROMPT_RULE
)

ENCOUNTER_PROMPT_SYS = (
    "你是文生图提示词工程师。把给定的 TRPG 遭遇战敌人转成一行**英文** Stable Diffusion 提示词："
    "描绘紧张的遭遇场面（horror creature encounter, dramatic composition），按敌方"
    "描述刻画其形貌与压迫感，按给定年代取环境质感。不要出现真实人名，不要引号，只输出提示词本身。"
    + SAFETY_PROMPT_RULE
)

CLUE_PROMPT_SYS = (
    "你是文生图提示词工程师。把给定的 TRPG 线索转成一行**英文** Stable Diffusion 提示词："
    "描绘这件线索物证本身的特写画面——材质、细节、陈放环境与年代质感（evidence close-up, "
    "dim lighting）。画风词不用写，系统会统一追加。不要出现人物面孔与真实人名，不要引号，只输出提示词本身。"
    + SAFETY_PROMPT_RULE
)

_TARGETS = {
    "scene": ("scenes", "image", SCENE_PROMPT_SYS),
    "npc": ("npcs", "portrait", NPC_PROMPT_SYS),
    "clue": ("clues", "image", CLUE_PROMPT_SYS),
}


def _target(module: Module, kind: str, item_id: str, field: str | None = None) -> tuple[dict, str, str, str]:
    config = _TARGETS.get(kind)
    if config is None:
        raise ValueError("不支持的图片类型")
    list_field, expected_field, prompt_sys = config
    allowed_fields = (
        ("portrait", "encounter_image") if kind == "npc"
        else ((expected_field, "image_variant") if kind == "scene" else (expected_field,))
    )
    if field and field not in allowed_fields:
        raise ValueError("图片字段与类型不匹配")
    target_field = field or expected_field
    for item in getattr(module, list_field, None) or []:
        if isinstance(item, dict) and str(item.get("id") or "") == str(item_id):
            return item, list_field, target_field, (
                ENCOUNTER_PROMPT_SYS if target_field == "encounter_image" else prompt_sys
            )
    raise LookupError("模组图片条目不存在")


def image_url_available(url: str | None) -> bool:
    """只把本地图片 URL 且对应文件存在视为可复用缓存。"""
    value = str(url or "").strip()
    if not value.startswith("/api/images/"):
        return False
    name = value.rsplit("/", 1)[-1]
    return bool(_IMAGE_NAME_RE.fullmatch(name)) and (image_store.IMAGES_DIR / name).is_file()


def _prompt_user(kind: str, item: dict, module: Module, field: str) -> str:
    era = str((module.world_setting or {}).get("era") or "1920s")
    if kind == "scene":
        return (
            f"场景：{item.get('title') or item.get('name') or item.get('id') or ''}\n"
            f"年代：{era}\n危险度：{item.get('danger') or ''}\n"
            f"氛围：{item.get('atmosphere') or ''}\n"
            f"描述：{str(item.get('description') or '')[:600]}"
        )
    if kind == "npc":
        if field == "encounter_image":
            return (
                f"敌方：{item.get('name') or item.get('id') or ''}\n年代：{era}\n"
                f"形貌与能力：{str(item.get('description') or '')[:400]}\n"
                f"武器/攻击方式：{str(item.get('weapon') or '')[:200]}"
            )
        return (
            f"NPC：{item.get('name') or item.get('id') or ''}\n年代：{era}\n"
            f"外貌与身份：{str(item.get('description') or '')[:400]}\n"
            f"性格：{str(item.get('personality') or '')[:200]}"
        )
    return (
        f"线索：{item.get('name') or item.get('id') or ''}\n年代：{era}\n"
        f"内容：{str(item.get('description') or '')[:600]}"
    )


#: 写回模组 JSON 的互斥锁。scenes/npcs/clues 是整列 JSON 字段，写回是「读整列 → 改一项 →
#: 整列写回」，两个请求重叠就会互相覆盖——后提交的那份里带着别人写之前的旧快照。
#: 一次给多个条目点「重新生成」正会触发：实测三张并发只有最后一张留在库里，前两张的图直接丢了。
_write_back_lock = asyncio.Lock()


async def write_back_image_url(
    db: Session,
    module: Module,
    kind: str,
    item_id: str,
    field: str | None,
    url: str,
    visual_state_key: str | None = None,
) -> bool:
    """把一个图片 URL 原子地写回模组 JSON 的对应条目。生成与手动上传共用这一条写回路径。

    走 `_target` 做校验：类型、条目存在性、字段与类型是否匹配，三者任一不合都会抛，
    避免上传接口绕开生成路径已有的那套约束、把图写到不该写的字段上。

    并发安全靠两件事合起来：进程内的 `_write_back_lock` 保证「读—改—写」不被别人插进来，
    锁内的 `expire` 丢掉本会话可能持有的旧快照、强制重新 SELECT，这样才读得到别的请求
    刚提交的结果。少任何一个，多图同时生成都会丢图。
    """
    _item, list_field, expected_field, _sys = _target(module, kind, item_id, field)
    state_key = str(visual_state_key or "").strip()
    if kind == "scene" and expected_field == "image_variant" and (not state_key or state_key == "base"):
        raise ValueError("状态图片缺少 visual_state_key")

    async with _write_back_lock:
        # 先结束本会话可能开着的事务，再作废快照：否则接下来的读仍会命中进入本请求时的旧值。
        db.commit()
        db.expire(module)
        items = [dict(v) if isinstance(v, dict) else v for v in (getattr(module, list_field, None) or [])]
        for value in items:
            if isinstance(value, dict) and str(value.get("id") or "") == str(item_id):
                if kind == "scene" and expected_field == "image_variant":
                    variants = dict(value.get("image_variants") or {})
                    variants[state_key] = url
                    value["image_variants"] = variants
                else:
                    value[expected_field] = url
                setattr(module, list_field, items)
                db.commit()
                db.refresh(module)
                return True
    return False


async def regenerate_module_image(
    db: Session,
    module: Module,
    kind: str,
    item_id: str,
    field: str | None = None,
    visual_state_key: str | None = None,
    force: bool = False,
) -> str | None:
    """重新生成一个模组图片，并将新 URL 原子地写回模组 JSON。

    ``force=False``（默认）是**自愈**语义：图片文件还在就直接复用，只补那些指向已失效
    文件的条目——这条路径由 <img onError> 自动触发，不该每次报错都重花一次生图的钱。
    ``force=True`` 是**用户点了「重新生成」**：必须真的重出一张，否则点了跟没点一样。
    """
    item, list_field, expected_field, prompt_sys = _target(module, kind, item_id, field)
    if kind == "scene" and expected_field == "image_variant":
        state_key = str(visual_state_key or "").strip()
        if not state_key or state_key == "base":
            raise ValueError("状态图片缺少 visual_state_key")
        cached = str((item.get("image_variants") or {}).get(state_key) or "").strip()
    else:
        state_key = ""
        cached = str(item.get(expected_field) or "").strip()
    if not force and image_url_available(cached):
        return cached

    # 提示词用文本模型写、图用生图配置出——两者各走各的配置，互不牵连。
    image_llm = get_image_llm()
    if not image_llm.supports_image_gen():
        return None
    # 写提示词与出图必须用**同一份**画风：分别取两次，中途改了模组画风就会一半新一半旧。
    suffix = style_suffix_for(module)
    try:
        raw = await get_fast_llm().complete(
            [
                {"role": "system", "content": prompt_sys + style_discipline(suffix)},
                {"role": "user", "content": _prompt_user(kind, item, module, expected_field)},
            ],
            temperature=0.7,
        )
        prompt = trim_prompt(raw)
        if not prompt:
            return None
        b64 = await image_llm.generate_image(f"{prompt}, {suffix}")
        if not b64:
            return None
        url = image_store.save_image_b64(b64)
        if not url:
            return None
        if not await write_back_image_url(db, module, kind, item_id, field, url, state_key):
            return None
        return url
    except Exception:  # noqa: BLE001 — 图片是增强能力，失败时由调用方返回可读错误
        logger.exception("模组图片重新生成失败：module=%s kind=%s item=%s", module.id, kind, item_id)
        return None
