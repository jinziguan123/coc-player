"""AI 供应商配置的**存储层**：读写 ``ai_settings.json``，并对外提供「当前该用哪个配置」。

**为什么它不在 api/。** 这些配置的消费者是 ``ai/llm_factory``（选 Provider）、
``ai/context``（按窗口算预算）、``ai/image_gen``（选生图后端）与编排服务
（``kp_tool_loop`` 判断要不要走工具路径）——全是业务侧。此前实现寄生在
``api/ai_settings.py`` 里，导致 ``services/`` 与 ``ai/`` 反过来 import ``api/``：
分层倒置，也让「配置从哪来」这件事得翻 HTTP 路由文件才说得清。

现在 ``api/ai_settings.py`` 只剩 HTTP 端点（增删改、连通性自测、掩码展示），
存储与选取一律在这里。依赖方向因此收敛为 ``api → ai``、``编排服务 → ai``。

**打桩口径**：调用方一律 ``from app.ai import profile_store`` 后按模块属性调用
（``profile_store.load_active_profile()``），不要 ``from ... import load_active_profile``。
这样测试只需 patch 本模块一处，就对全部调用方生效。
"""

from __future__ import annotations

import json
import uuid

from pydantic import BaseModel

from app.config import settings

# 配置文件与数据库同目录：dev 下是 server/ai_settings.json（行为不变）；打包运行时落到用户
# 可写的 app-data（跟随 settings.db_path），否则会写进 PyInstaller 临时目录（sys._MEIPASS，
# 退出即删）导致配置读不到 / 重启丢失。
SETTINGS_FILE = settings.db_path.parent / "ai_settings.json"


# ---------- 数据模型 ----------

class AIProfile(BaseModel):
    id: str = ""
    name: str = ""
    protocol: str = "openai"  # "openai" | "anthropic"
    base_url: str = ""
    model_name: str = ""
    api_key: str = ""
    is_active: bool = False
    # 快模型标记：planner/AI 队友/滚动摘要/幕后推演等结构化副任务改走该配置（省时提速）；
    # KP 主叙事与 NPC 台词恒走激活配置。全部未标记 = 副任务也走激活配置（旧行为）。
    is_fast: bool = False
    vision: bool = False  # 是否支持多模态（看图）。显式开关，覆盖按模型名的启发式判断
    # 「视觉模型」槽位：解析扫描件/图文模组时改走此配置。与上面的 vision 是两回事——
    # vision 说的是「这个模型会不会看图」，is_vision 说的是「看图的活派给谁」。
    # 主模型多为纯文本（带团要的是文笔与工具调用，不是眼睛），从前这意味着图文模组直接解析不了；
    # 有了槽位就能：带团用纯文本模型，解析模组时自动切到视觉模型（含本机部署的）。
    is_vision: bool = False
    # KP 生成走 agent loop（标准工具调用）新路径的开关。**默认开启**（tool_use 为治本方向，
    # 台词走 say() 结构化出口）；仅当 Provider 支持工具（supports_tools）时才实际生效，否则安全
    # 回退旧正则指令路径，见 kp_tool_loop._tool_loop_active。
    use_tool_calls: bool = True
    # 模型上下文窗口（token）。0 = 未知，由 resolve_context_window 按模型名启发式回落。
    # 用于「上下文占用预估」判断模型是否还撑得住继续跑团。
    context_window: int = 0
    # 关闭模型思考：下发 {"thinking": {"type": "disabled"}}（DeepSeek 等在 OpenAI 兼容格式下
    # 的思考模式开关）。**这是唯一可靠的提速手段**——思考默认是开的、effort 默认 high，而思考
    # 内容会被 complete() 丢弃（只收 delta.content），时间照花、产物照扔。
    # 实测 deepseek-v4-flash：默认思考 73~140 token，置本项后恒为 0。
    # 不认这个字段的服务会忽略它；只在 OpenAI 兼容协议下下发。
    thinking_disabled: bool = False
    # 思考强度（reasoning_effort）：low/high/max。空=不下发、用模型默认档（DeepSeek 默认 high）。
    # 注意它**只调强度、不能关思考**，要关请用上面的 thinking_disabled。
    # 仅 OpenAI 兼容协议生效；设了会一并省略 temperature（推理模型多拒绝/忽略它）。
    reasoning_effort: str = ""
    # ── 以下生图字段已废弃：生图配置已独立成 ImageProfile（见下），不再随文本配置走。
    # 保留纯粹是为了读得懂旧 ai_settings.json 并把它们迁移出去（_migrate_image_profiles），
    # 迁移完成后不再有任何读取方；新建/更新配置也不再写入。
    image_model: str = ""
    image_base_url: str = ""
    image_api_key: str = ""
    image_backend: str = "openai"
    comfyui_base_url: str = ""
    comfyui_workflow: str = ""


class ImageProfile(BaseModel):
    """生图配置：与文本模型完全解耦，独立增删改与激活。

    从前生图字段寄生在文本 AIProfile 上，后果有二：①同一台 ComfyUI 要在每个文本配置里
    重抄一遍；②`image_model` 只传给 OpenAICompatProvider，Anthropic 协议的配置填了也**静默
    失效**。拆开之后，用什么文本模型跑团与用什么后端出图互不相干。
    """

    id: str = ""
    name: str = ""
    # 后端：openai=OpenAI Images 端点；comfyui=内网 ComfyUI 工作流
    backend: str = "openai"
    is_active: bool = False
    # OpenAI Images：模型名（dall-e-3 / gpt-image-1…）、端点地址与密钥
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    # ComfyUI：实例地址 + API 格式工作流 JSON（占位 PLACEHOLDER_POSITIVE/NEGATIVE；空=内置默认）
    comfyui_base_url: str = ""
    comfyui_workflow: str = ""


# ---------- 上下文窗口解析 ----------

# 常见模型的上下文窗口（token）——用于用户没显式配 context_window 时的启发式回落。
# 只做子串匹配，覆盖主流；未命中回落 _DEFAULT_CONTEXT_WINDOW（偏保守但 ≥ 现有上下文预算）。
_MODEL_CONTEXT_WINDOWS: list[tuple[str, int]] = [
    ("claude", 200_000),
    ("gpt-4o", 128_000),
    ("gpt-4.1", 1_000_000),
    ("o1", 200_000),
    ("o3", 200_000),
    ("gemini", 1_000_000),
    # 具体型号必须排在通用前缀之前——匹配是「顺序取首个命中的子串」。
    # deepseek-v4 起窗口放到 1M；v3/r1 仍是 64K，故不能只留一条通用 deepseek。
    ("deepseek-v4", 1_000_000),
    ("deepseek", 65_536),
    ("qwen", 131_072),
    ("glm", 131_072),
    ("moonshot", 131_072),
    ("kimi", 131_072),
    ("doubao", 131_072),
    ("yi", 65_536),
]
_DEFAULT_CONTEXT_WINDOW = 65_536


def resolve_context_window(profile: "AIProfile | None") -> int:
    """解析模型的有效上下文窗口：显式配置优先，否则按模型名启发式，最后回落默认值。"""
    if profile and profile.context_window and profile.context_window > 0:
        return profile.context_window
    name = (profile.model_name if profile else "").lower()
    for key, window in _MODEL_CONTEXT_WINDOWS:
        if key in name:
            return window
    return _DEFAULT_CONTEXT_WINDOW


# ---------- 存储层 ----------

def _load_raw() -> dict:
    """读取原始 JSON，支持旧格式自动迁移"""
    if not SETTINGS_FILE.exists():
        return {"profiles": []}
    try:
        data = json.loads(SETTINGS_FILE.read_text("utf-8"))
    except Exception:
        return {"profiles": []}

    # 旧格式迁移：{base_url, model_name, api_key} -> {profiles: [...]}
    if "profiles" not in data and ("base_url" in data or "model_name" in data or "api_key" in data):
        old_profile = AIProfile(
            id=str(uuid.uuid4()),
            name="默认配置（迁移）",
            protocol="openai",
            base_url=data.get("base_url", ""),
            model_name=data.get("model_name", ""),
            api_key=data.get("api_key", ""),
            is_active=True,
        )
        new_data = {"profiles": [old_profile.model_dump()]}
        _save_raw(new_data)
        return new_data

    # 生图配置独立化迁移：把寄生在文本配置上的 image_*/comfyui_* 抽成独立的 image_profiles
    if "image_profiles" not in data:
        data["image_profiles"] = _migrate_image_profiles(data.get("profiles", []))
        _save_raw(data)

    return data


def _image_profile_name(backend: str, model: str, host: str, taken: set[str]) -> str:
    """给迁移出来的生图配置起个一眼能认出的名字，重名时加序号。"""
    base = f"ComfyUI（{host}）" if backend == "comfyui" else (model or "OpenAI 生图")
    name, n = base, 2
    while name in taken:
        name, n = f"{base} {n}", n + 1
    return name


def _migrate_image_profiles(raw_profiles: list[dict]) -> list[dict]:
    """从旧文本配置里抽出生图设置，**按内容去重**后成为独立的生图配置列表。

    去重是重点：同一台 ComfyUI 过去要在每个文本配置里各抄一份（实测一个用户的四条文本
    配置里有两条填着同一个地址），拆出来时必须合并成一条，否则界面上会并排出现两个
    一模一样的生图配置。

    地址与密钥在这里就地固化（旧语义是「留空则回落文本配置的 base_url/api_key」）——
    拆开之后没有文本配置可回落了，迁移时不解析就等于丢配置。
    激活项取自当时激活的文本配置所用的那份生图设置，保证升级前后出图行为不变。
    """
    out: list[dict] = []
    by_key: dict[tuple, dict] = {}
    active_key: tuple | None = None
    for raw in raw_profiles:
        backend = str(raw.get("image_backend") or "openai").strip()
        comfy_url = str(raw.get("comfyui_base_url") or "").strip()
        model = str(raw.get("image_model") or "").strip()
        if backend == "comfyui":
            if not comfy_url:
                continue
            key = ("comfyui", comfy_url, str(raw.get("comfyui_workflow") or "").strip())
        else:
            if not model:
                continue
            key = (
                "openai", model,
                str(raw.get("image_base_url") or raw.get("base_url") or "").strip(),
            )
        if raw.get("is_active"):
            active_key = key
        if key in by_key:
            continue
        host = comfy_url.split("//")[-1].split("/")[0] or comfy_url
        profile = ImageProfile(
            id=str(uuid.uuid4()),
            name=_image_profile_name(backend, model, host, {p["name"] for p in out}),
            backend="comfyui" if backend == "comfyui" else "openai",
            model=model,
            # 旧语义：生图地址/密钥留空即复用文本配置的那份，迁移时固化下来
            base_url=str(raw.get("image_base_url") or raw.get("base_url") or "").strip(),
            api_key=str(raw.get("image_api_key") or raw.get("api_key") or "").strip(),
            comfyui_base_url=comfy_url,
            comfyui_workflow=str(raw.get("comfyui_workflow") or ""),
        ).model_dump()
        by_key[key] = profile
        out.append(profile)
    if out:
        # 原本激活的文本配置用哪份生图设置，就激活哪份；它没配生图则回落第一条。
        chosen = by_key.get(active_key) if active_key else None
        (chosen or out[0])["is_active"] = True
    return out


def _save_raw(data: dict) -> None:
    SETTINGS_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _load_profiles() -> list[AIProfile]:
    data = _load_raw()
    return [AIProfile(**p) for p in data.get("profiles", [])]


def _save_profiles(profiles: list[AIProfile]) -> None:
    # 只覆盖 profiles 段：生图配置与它同住一个文件，整份重写会把 image_profiles 抹掉。
    data = _load_raw()
    data["profiles"] = [p.model_dump() for p in profiles]
    _save_raw(data)


def _load_image_profiles() -> list[ImageProfile]:
    return [ImageProfile(**p) for p in _load_raw().get("image_profiles", [])]


def _save_image_profiles(profiles: list[ImageProfile]) -> None:
    data = _load_raw()
    data["image_profiles"] = [p.model_dump() for p in profiles]
    _save_raw(data)


# ---------- 公开选取函数（供 get_llm / 上下文预算 / 生图调用） ----------

def load_active_profile() -> AIProfile | None:
    """返回当前激活的配置，没有则返回 None"""
    profiles = _load_profiles()
    for p in profiles:
        if p.is_active:
            return p
    return None


def load_fast_profile() -> AIProfile | None:
    """返回标记为「快模型」的配置；未标记或配置不完整（缺 key/model）返回 None（回落主模型）。"""
    for p in _load_profiles():
        if p.is_fast and p.api_key and p.model_name:
            return p
    return None


def load_vision_profile() -> AIProfile | None:
    """返回标记为「视觉模型」的配置；未标记或配置不完整（缺 key/model）返回 None（回落主模型）。

    只负责「派给谁」，不负责「它到底会不会看图」——后者仍由 Provider 的 supports_vision()
    判定，槽位里放了个纯文本模型照样会被挡下来（错误文案会指回这里）。
    """
    for p in _load_profiles():
        if p.is_vision and p.api_key and p.model_name:
            return p
    return None


def load_active_image_profile() -> ImageProfile | None:
    """返回激活的生图配置；没有则 None（=本机不出图，配图静默跳过）。"""
    for p in _load_image_profiles():
        if p.is_active:
            return p
    return None
