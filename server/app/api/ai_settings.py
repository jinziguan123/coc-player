"""AI 模型多配置管理 API"""

from __future__ import annotations

import json
import time
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import require_local_client
from app.config import settings
from app.services import ai_quota

# 配置文件与数据库同目录：dev 下是 server/ai_settings.json（行为不变）；打包运行时落到用户
# 可写的 app-data（跟随 settings.db_path），否则会写进 PyInstaller 临时目录（sys._MEIPASS，
# 退出即删）导致配置读不到 / 重启丢失。
SETTINGS_FILE = settings.db_path.parent / "ai_settings.json"

# 配置的增删改查仅限房主本机：这里既能读到明文 API key，也能改 base_url（可被指向他人服务）。
router = APIRouter(
    prefix="/api/settings", tags=["settings"],
    dependencies=[Depends(require_local_client)],
)

# 例外：只读的「配没配好」探针对客人开放。客人要判断的正是**房主**有没有配好 AI
# （AI 调用发生在房主机器上），把它一并锁掉会让客人的开局前置校验永远失败。
# 它只返回一个布尔和配置昵称，不含任何凭据。
public_router = APIRouter(prefix="/api/settings", tags=["settings"])


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


class ImageProfileCreate(BaseModel):
    name: str
    backend: str = "openai"
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    comfyui_base_url: str = ""
    comfyui_workflow: str = ""


class ImageProfileUpdate(BaseModel):
    name: str | None = None
    backend: str | None = None
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    comfyui_base_url: str | None = None
    comfyui_workflow: str | None = None


class AIProfileCreate(BaseModel):
    name: str
    protocol: str = "openai"
    base_url: str = ""
    model_name: str = ""
    api_key: str = ""
    vision: bool = False
    use_tool_calls: bool = True
    context_window: int = 0
    thinking_disabled: bool = False
    reasoning_effort: str = ""


class AIProfileUpdate(BaseModel):
    name: str | None = None
    protocol: str | None = None
    base_url: str | None = None
    model_name: str | None = None
    api_key: str | None = None
    vision: bool | None = None
    use_tool_calls: bool | None = None
    context_window: int | None = None
    thinking_disabled: bool | None = None
    reasoning_effort: str | None = None


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


class TestResult(BaseModel):
    success: bool
    message: str
    latency_ms: int = 0


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


def _mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return key[:4] + "****" + key[-4:]


# ---------- 公开函数（供 get_llm 调用） ----------

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


# ---------- API 端点 ----------

class AIStatus(BaseModel):
    configured: bool
    name: str | None = None


@public_router.get("/ai/status", response_model=AIStatus)
def ai_status():
    """开局前置校验：是否存在可用的激活 AI 配置（有 api_key + model_name）。

    前端在创建会话/开场前调用，未配置时引导用户去设置页，避免开场直接失败还无从下手。
    """
    p = load_active_profile()
    ok = bool(p and p.api_key and p.model_name)
    return AIStatus(configured=ok, name=p.name if p else None)


@router.get("/ai/profiles", response_model=list[AIProfile])
def list_profiles():
    """列出所有配置（api_key 掩码处理）"""
    profiles = _load_profiles()
    for p in profiles:
        p.api_key = _mask_key(p.api_key)
    return profiles


@router.post("/ai/profiles", response_model=AIProfile)
def create_profile(body: AIProfileCreate):
    """新建配置"""
    profiles = _load_profiles()
    new_profile = AIProfile(
        id=str(uuid.uuid4()),
        name=body.name,
        protocol=body.protocol,
        base_url=body.base_url,
        model_name=body.model_name,
        api_key=body.api_key,
        vision=body.vision,
        use_tool_calls=body.use_tool_calls,
        context_window=body.context_window,
        thinking_disabled=body.thinking_disabled,
        reasoning_effort=body.reasoning_effort,
        is_active=len(profiles) == 0,  # 第一个配置自动激活
    )
    profiles.append(new_profile)
    _save_profiles(profiles)
    new_profile.api_key = _mask_key(new_profile.api_key)
    return new_profile


@router.put("/ai/profiles/{profile_id}", response_model=AIProfile)
def update_profile(profile_id: str, body: AIProfileUpdate):
    """更新配置。如果 api_key 包含掩码字符，保留旧 key"""
    profiles = _load_profiles()
    target = None
    for p in profiles:
        if p.id == profile_id:
            target = p
            break
    if not target:
        raise HTTPException(status_code=404, detail="配置不存在")

    if body.name is not None:
        target.name = body.name
    if body.protocol is not None:
        target.protocol = body.protocol
    if body.base_url is not None:
        target.base_url = body.base_url
    if body.model_name is not None:
        target.model_name = body.model_name
    if body.vision is not None:
        target.vision = body.vision
    if body.use_tool_calls is not None:
        target.use_tool_calls = body.use_tool_calls
    if body.context_window is not None:
        target.context_window = body.context_window
    if body.thinking_disabled is not None:
        target.thinking_disabled = body.thinking_disabled
    if body.reasoning_effort is not None:
        target.reasoning_effort = body.reasoning_effort
    if body.api_key is not None:
        # 如果包含掩码字符，说明前端没有修改 key，保留旧值
        if "****" not in body.api_key:
            target.api_key = body.api_key

    _save_profiles(profiles)
    target.api_key = _mask_key(target.api_key)
    return target


@router.delete("/ai/profiles/{profile_id}")
def delete_profile(profile_id: str):
    """删除配置"""
    profiles = _load_profiles()
    new_profiles = [p for p in profiles if p.id != profile_id]
    if len(new_profiles) == len(profiles):
        raise HTTPException(status_code=404, detail="配置不存在")
    # 如果删除的是激活的配置，激活第一个剩余配置
    if not any(p.is_active for p in new_profiles) and new_profiles:
        new_profiles[0].is_active = True
    _save_profiles(new_profiles)
    return {"status": "ok"}


@router.post("/ai/profiles/{profile_id}/activate")
def activate_profile(profile_id: str):
    """设为激活配置"""
    profiles = _load_profiles()
    found = False
    for p in profiles:
        if p.id == profile_id:
            p.is_active = True
            found = True
        else:
            p.is_active = False
    if not found:
        raise HTTPException(status_code=404, detail="配置不存在")
    _save_profiles(profiles)
    return {"status": "ok"}


@router.post("/ai/profiles/{profile_id}/set-fast")
def set_fast_profile(profile_id: str):
    """把某配置标记为「快模型」（结构化副任务用）；再点同一个 = 取消标记（回落主模型）。"""
    profiles = _load_profiles()
    found = False
    for p in profiles:
        if p.id == profile_id:
            p.is_fast = not p.is_fast   # 幂等开关：重复点击即取消
            found = True
        else:
            p.is_fast = False
    if not found:
        raise HTTPException(status_code=404, detail="配置不存在")
    _save_profiles(profiles)
    return {"status": "ok", "is_fast": any(p.is_fast for p in profiles)}


@router.post("/ai/profiles/{profile_id}/set-vision")
def set_vision_profile(profile_id: str):
    """把某配置标记为「视觉模型」（解析扫描件/图文模组用）；再点同一个 = 取消标记（回落主模型）。"""
    profiles = _load_profiles()
    found = False
    for p in profiles:
        if p.id == profile_id:
            p.is_vision = not p.is_vision   # 幂等开关：重复点击即取消
            found = True
        else:
            p.is_vision = False
    if not found:
        raise HTTPException(status_code=404, detail="配置不存在")
    _save_profiles(profiles)
    return {"status": "ok", "is_vision": any(p.is_vision for p in profiles)}


@router.get("/ai/profiles/{profile_id}/key")
def reveal_profile_key(profile_id: str):
    """返回该配置的完整 API Key（明文），供设置页「显示/复制」用。

    本应用为全本地部署，密钥本就存于本地 ai_settings.json——此端点只是把「打开文件看」
    变成界面操作，不扩大密钥的暴露面。"""
    for p in _load_profiles():
        if p.id == profile_id:
            return {"api_key": p.api_key}
    raise HTTPException(status_code=404, detail="配置不存在")


@router.post("/ai/profiles/{profile_id}/duplicate", response_model=AIProfile)
def duplicate_profile(profile_id: str):
    """一键复制配置：完整拷贝（含真实 key），命名「X 副本」，不激活、不标快模型。

    典型用途：复制主配置后只改模型名，做成「快模型」变体，免得重填地址和密钥。"""
    profiles = _load_profiles()
    src = next((p for p in profiles if p.id == profile_id), None)
    if not src:
        raise HTTPException(status_code=404, detail="配置不存在")
    dup = src.model_copy(update={
        "id": str(uuid.uuid4()),
        "name": f"{src.name} 副本",
        "is_active": False,
        "is_fast": False,
    })
    profiles.append(dup)
    _save_profiles(profiles)
    resp = dup.model_copy()
    resp.api_key = _mask_key(resp.api_key)
    return resp


@router.post("/ai/profiles/{profile_id}/test", response_model=TestResult)
async def test_profile(profile_id: str):
    """测试配置连接"""
    profiles = _load_profiles()
    target = None
    for p in profiles:
        if p.id == profile_id:
            target = p
            break
    if not target:
        raise HTTPException(status_code=404, detail="配置不存在")

    start = time.time()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            if target.protocol == "anthropic":
                result = await _test_anthropic(client, target)
            else:
                result = await _test_openai(client, target)
        latency = int((time.time() - start) * 1000)
        return TestResult(success=True, message=result, latency_ms=latency)
    except httpx.TimeoutException:
        latency = int((time.time() - start) * 1000)
        return TestResult(success=False, message="连接超时", latency_ms=latency)
    except httpx.HTTPStatusError as e:
        latency = int((time.time() - start) * 1000)
        detail = ""
        try:
            err_body = e.response.json()
            detail = err_body.get("error", {}).get("message", "") or str(err_body)
        except Exception:
            detail = e.response.text[:200]
        return TestResult(
            success=False,
            message=f"HTTP {e.response.status_code}: {detail}",
            latency_ms=latency,
        )
    except Exception as e:
        latency = int((time.time() - start) * 1000)
        return TestResult(success=False, message=str(e), latency_ms=latency)


def _clean_http_error(e: httpx.HTTPStatusError) -> str:
    """把 HTTP 错误体压成一句可读信息：JSON 取 error.message；HTML 错误页（网关 5xx）只报状态，
    不把整页 HTML 糊到提示里。"""
    body = (e.response.text or "").strip()
    try:
        detail = e.response.json().get("error", {}).get("message", "")
        if detail:
            return f"HTTP {e.response.status_code}: {detail}"
    except Exception:
        pass
    if body[:64].lstrip().lower().startswith(("<!doctype", "<html")):
        return f"HTTP {e.response.status_code}：网关返回了 HTML 错误页——该地址多半不是可用的 images 端点，或分组/供应商此刻不可用。"
    return f"HTTP {e.response.status_code}: {body[:160]}"


# ── 生图配置（与文本配置并列的一套 CRUD） ───────────────────────────────────

@router.get("/ai/image-profiles", response_model=list[ImageProfile])
def list_image_profiles():
    profiles = _load_image_profiles()
    for p in profiles:
        p.api_key = _mask_key(p.api_key)
    return profiles


@router.post("/ai/image-profiles", response_model=ImageProfile)
def create_image_profile(body: ImageProfileCreate):
    profiles = _load_image_profiles()
    created = ImageProfile(
        id=str(uuid.uuid4()),
        name=body.name,
        backend="comfyui" if body.backend == "comfyui" else "openai",
        model=body.model,
        base_url=body.base_url,
        api_key=body.api_key,
        comfyui_base_url=body.comfyui_base_url,
        comfyui_workflow=body.comfyui_workflow,
        is_active=len(profiles) == 0,   # 第一个生图配置自动激活
    )
    profiles.append(created)
    _save_image_profiles(profiles)
    created.api_key = _mask_key(created.api_key)
    return created


@router.put("/ai/image-profiles/{profile_id}", response_model=ImageProfile)
def update_image_profile(profile_id: str, body: ImageProfileUpdate):
    profiles = _load_image_profiles()
    target = next((p for p in profiles if p.id == profile_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="生图配置不存在")
    if body.name is not None:
        target.name = body.name
    if body.backend is not None:
        target.backend = "comfyui" if body.backend == "comfyui" else "openai"
    if body.model is not None:
        target.model = body.model
    if body.base_url is not None:
        target.base_url = body.base_url
    if body.comfyui_base_url is not None:
        target.comfyui_base_url = body.comfyui_base_url
    if body.comfyui_workflow is not None:
        target.comfyui_workflow = body.comfyui_workflow
    # 含掩码字符 = 前端没改过这个框，保留旧值（与文本配置同一约定）
    if body.api_key is not None and "****" not in body.api_key:
        target.api_key = body.api_key
    _save_image_profiles(profiles)
    target.api_key = _mask_key(target.api_key)
    return target


@router.delete("/ai/image-profiles/{profile_id}")
def delete_image_profile(profile_id: str):
    profiles = _load_image_profiles()
    remaining = [p for p in profiles if p.id != profile_id]
    if len(remaining) == len(profiles):
        raise HTTPException(status_code=404, detail="生图配置不存在")
    # 删掉的是激活项就顺位激活第一条；一条不剩 = 本机不出图（合法状态，配图静默跳过）
    if remaining and not any(p.is_active for p in remaining):
        remaining[0].is_active = True
    _save_image_profiles(remaining)
    return {"status": "ok"}


@router.post("/ai/image-profiles/{profile_id}/activate")
def activate_image_profile(profile_id: str):
    profiles = _load_image_profiles()
    if not any(p.id == profile_id for p in profiles):
        raise HTTPException(status_code=404, detail="生图配置不存在")
    for p in profiles:
        p.is_active = p.id == profile_id
    _save_image_profiles(profiles)
    return {"status": "ok"}


@router.get("/ai/image-profiles/{profile_id}/key")
def reveal_image_profile_key(profile_id: str):
    """返回该生图配置的完整 API Key（明文），供设置页「显示/复制」用。与文本配置同理。"""
    for p in _load_image_profiles():
        if p.id == profile_id:
            return {"api_key": p.api_key}
    raise HTTPException(status_code=404, detail="生图配置不存在")


@router.post("/ai/image-profiles/{profile_id}/test", response_model=TestResult)
async def test_image_profile(profile_id: str):
    """真出一张图验证该生图配置——用的是运行时同一条装配路径（image_generator_from_profile），
    所以「测试通过」意味着跑团时也确实出得来图。"""
    from app.ai.image_gen import image_generator_from_profile

    target = next((p for p in _load_image_profiles() if p.id == profile_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="生图配置不存在")
    if target.backend == "comfyui" and not target.comfyui_base_url.strip():
        return TestResult(success=False, message="未填写 ComfyUI 地址", latency_ms=0)
    if target.backend != "comfyui" and not target.model.strip():
        return TestResult(success=False, message="未填写生图模型名", latency_ms=0)

    start = time.time()
    try:
        b64 = await image_generator_from_profile(target).generate_image(
            "a simple red apple on a wooden table, photo",
        )
        ms = int((time.time() - start) * 1000)
        if b64:
            return TestResult(
                success=True, message=f"生图成功（{len(b64) // 1024}KB）", latency_ms=ms,
            )
        # 生成器把异常都吞成 None（跑团时绝不因配图中断），所以这里只能给排查方向。
        hint = (
            "检查 ComfyUI 地址可达性、工作流 JSON 与占位符"
            if target.backend == "comfyui"
            else "检查模型名、端点地址与密钥"
        )
        return TestResult(success=False, message=f"生图失败：{hint}（详见后端日志）", latency_ms=ms)
    except httpx.HTTPStatusError as e:
        return TestResult(success=False, message=_clean_http_error(e),
                          latency_ms=int((time.time() - start) * 1000))
    except Exception as e:
        return TestResult(success=False, message=str(e),
                          latency_ms=int((time.time() - start) * 1000))


async def _test_openai(client: httpx.AsyncClient, profile: AIProfile) -> str:
    """使用 OpenAI 兼容协议测试连接"""
    base = profile.base_url.rstrip("/") if profile.base_url else "https://api.deepseek.com"
    url = f"{base}/chat/completions"
    headers = {
        "Authorization": f"Bearer {profile.api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": profile.model_name,
        "messages": [{"role": "user", "content": "回复OK"}],
        "max_tokens": 16,
        "temperature": 0,
    }
    # 按真实调用口径带上思考开关与强度，让连接测试如实反映能否用
    if getattr(profile, "thinking_disabled", False):
        payload["thinking"] = {"type": "disabled"}
    if getattr(profile, "reasoning_effort", "").strip():
        payload["reasoning_effort"] = profile.reasoning_effort.strip()
        payload.pop("temperature", None)
    resp = await client.post(url, headers=headers, json=payload)
    resp.raise_for_status()
    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    return f"连接成功: {content.strip()}"


async def _test_anthropic(client: httpx.AsyncClient, profile: AIProfile) -> str:
    """使用 Anthropic Messages API 测试连接"""
    base = profile.base_url.rstrip("/") if profile.base_url else "https://api.anthropic.com"
    url = f"{base}/v1/messages"
    headers = {
        "x-api-key": profile.api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    payload = {
        "model": profile.model_name,
        "messages": [{"role": "user", "content": "回复OK"}],
        "max_tokens": 16,
        "temperature": 0,
    }
    resp = await client.post(url, headers=headers, json=payload)
    resp.raise_for_status()
    data = resp.json()
    content = data["content"][0]["text"]
    return f"连接成功: {content.strip()}"


# ── 房间级 AI 配额 ──────────────────────────────────────────────────────────
# 挂在 /api/settings 下，因此自动继承本路由的「仅限房主本机」（ADR-007）——
# 配额是保护房主钱包的策略，当然只能房主自己改。


class AIQuotaPolicy(BaseModel):
    enabled: bool
    limit: str
    """`limits` 库的写法，如 "100/hour"、"20/minute"。"""


class AIQuotaUpdate(BaseModel):
    enabled: bool
    limit: str | None = None


@router.get("/ai/quota", response_model=AIQuotaPolicy)
def get_ai_quota() -> AIQuotaPolicy:
    return AIQuotaPolicy(**ai_quota.policy())


@router.put("/ai/quota", response_model=AIQuotaPolicy)
def update_ai_quota(data: AIQuotaUpdate) -> AIQuotaPolicy:
    return AIQuotaPolicy(**ai_quota.set_policy(data.enabled, data.limit))
