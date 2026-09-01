"""AI 模型多配置管理 API"""

from __future__ import annotations

import time
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.ai import profile_store
from app.ai.profile_store import AIProfile, ImageProfile
from app.api.deps import require_local_client
from app.services import ai_quota

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


class TestResult(BaseModel):
    success: bool
    message: str
    latency_ms: int = 0


class ModelsProbe(BaseModel):
    """问上游要模型清单时带的那点信息，取自表单当前的值。"""

    protocol: str = "openai"
    base_url: str = ""
    api_key: str = ""


class ModelsResult(BaseModel):
    success: bool
    models: list[str] = []
    message: str = ""


def _mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return key[:4] + "****" + key[-4:]


# ---------- API 端点 ----------

class AIStatus(BaseModel):
    configured: bool
    name: str | None = None


@public_router.get("/ai/status", response_model=AIStatus)
def ai_status():
    """开局前置校验：是否存在可用的激活 AI 配置（有 api_key + model_name）。

    前端在创建会话/开场前调用，未配置时引导用户去设置页，避免开场直接失败还无从下手。
    """
    p = profile_store.load_active_profile()
    ok = bool(p and p.api_key and p.model_name)
    return AIStatus(configured=ok, name=p.name if p else None)


@router.get("/ai/profiles", response_model=list[AIProfile])
def list_profiles():
    """列出所有配置（api_key 掩码处理）"""
    profiles = profile_store._load_profiles()
    for p in profiles:
        p.api_key = _mask_key(p.api_key)
    return profiles


@router.post("/ai/profiles", response_model=AIProfile)
def create_profile(body: AIProfileCreate):
    """新建配置"""
    profiles = profile_store._load_profiles()
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
    profile_store._save_profiles(profiles)
    new_profile.api_key = _mask_key(new_profile.api_key)
    return new_profile


@router.put("/ai/profiles/{profile_id}", response_model=AIProfile)
def update_profile(profile_id: str, body: AIProfileUpdate):
    """更新配置。如果 api_key 包含掩码字符，保留旧 key"""
    profiles = profile_store._load_profiles()
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

    profile_store._save_profiles(profiles)
    target.api_key = _mask_key(target.api_key)
    return target


@router.delete("/ai/profiles/{profile_id}")
def delete_profile(profile_id: str):
    """删除配置"""
    profiles = profile_store._load_profiles()
    new_profiles = [p for p in profiles if p.id != profile_id]
    if len(new_profiles) == len(profiles):
        raise HTTPException(status_code=404, detail="配置不存在")
    # 如果删除的是激活的配置，激活第一个剩余配置
    if not any(p.is_active for p in new_profiles) and new_profiles:
        new_profiles[0].is_active = True
    profile_store._save_profiles(new_profiles)
    return {"status": "ok"}


@router.post("/ai/profiles/{profile_id}/activate")
def activate_profile(profile_id: str):
    """设为激活配置"""
    profiles = profile_store._load_profiles()
    found = False
    for p in profiles:
        if p.id == profile_id:
            p.is_active = True
            found = True
        else:
            p.is_active = False
    if not found:
        raise HTTPException(status_code=404, detail="配置不存在")
    profile_store._save_profiles(profiles)
    return {"status": "ok"}


@router.post("/ai/profiles/{profile_id}/set-fast")
def set_fast_profile(profile_id: str):
    """把某配置标记为「快模型」（结构化副任务用）；再点同一个 = 取消标记（回落主模型）。"""
    profiles = profile_store._load_profiles()
    found = False
    for p in profiles:
        if p.id == profile_id:
            p.is_fast = not p.is_fast   # 幂等开关：重复点击即取消
            found = True
        else:
            p.is_fast = False
    if not found:
        raise HTTPException(status_code=404, detail="配置不存在")
    profile_store._save_profiles(profiles)
    return {"status": "ok", "is_fast": any(p.is_fast for p in profiles)}


@router.post("/ai/profiles/{profile_id}/set-vision")
def set_vision_profile(profile_id: str):
    """把某配置标记为「视觉模型」（解析扫描件/图文模组用）；再点同一个 = 取消标记（回落主模型）。"""
    profiles = profile_store._load_profiles()
    found = False
    for p in profiles:
        if p.id == profile_id:
            p.is_vision = not p.is_vision   # 幂等开关：重复点击即取消
            found = True
        else:
            p.is_vision = False
    if not found:
        raise HTTPException(status_code=404, detail="配置不存在")
    profile_store._save_profiles(profiles)
    return {"status": "ok", "is_vision": any(p.is_vision for p in profiles)}


@router.get("/ai/profiles/{profile_id}/key")
def reveal_profile_key(profile_id: str):
    """返回该配置的完整 API Key（明文），供设置页「显示/复制」用。

    本应用为全本地部署，密钥本就存于本地 ai_settings.json——此端点只是把「打开文件看」
    变成界面操作，不扩大密钥的暴露面。"""
    for p in profile_store._load_profiles():
        if p.id == profile_id:
            return {"api_key": p.api_key}
    raise HTTPException(status_code=404, detail="配置不存在")


@router.post("/ai/profiles/{profile_id}/duplicate", response_model=AIProfile)
def duplicate_profile(profile_id: str):
    """一键复制配置：完整拷贝（含真实 key），命名「X 副本」，不激活、不标快模型。

    典型用途：复制主配置后只改模型名，做成「快模型」变体，免得重填地址和密钥。"""
    profiles = profile_store._load_profiles()
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
    profile_store._save_profiles(profiles)
    resp = dup.model_copy()
    resp.api_key = _mask_key(resp.api_key)
    return resp


@router.post("/ai/profiles/{profile_id}/test", response_model=TestResult)
async def test_profile(profile_id: str):
    """测试配置连接"""
    profiles = profile_store._load_profiles()
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


@router.post("/ai/models", response_model=ModelsResult)
async def list_upstream_models(probe: ModelsProbe) -> ModelsResult:
    """问上游有哪些模型可用。

    模型名此前只能手打，而差一个横杠就是 404——报错还要等到真开团、KP 该说话的时候才
    冒出来。两种协议都有标准的 `GET …/models`，填好地址和密钥就能问出来。

    收的是**表单里当前的值**而不是 profile_id：新增配置时它还没存过，正是最需要这份
    清单的时候。编辑态下前端本来就会把真实密钥取回表单（见 `/profiles/{id}/key`），
    两种情形因此走同一条路。

    不少中转站不实现这个端点，返回 404/501 是常态而非故障——照直说「这个服务没提供
    清单，手填吧」，别让人以为是自己地址填错了。
    """
    base = (probe.base_url or "").strip().rstrip("/")
    key = (probe.api_key or "").strip()
    if probe.protocol == "anthropic":
        base = base or "https://api.anthropic.com"
        url = f"{base}/v1/models"
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    else:
        # OpenAI 兼容侧 base_url 就是 API 根（该带 /v1 的用户自己带），与 _test_openai 同口径
        base = base or "https://api.deepseek.com"
        url = f"{base}/models"
        headers = {"Authorization": f"Bearer {key}"}

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            payload = resp.json()
    except httpx.TimeoutException:
        return ModelsResult(success=False, message="连接超时")
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (404, 405, 501):
            return ModelsResult(
                success=False,
                message="这个服务没有提供模型清单接口，模型名请手动填写。",
            )
        return ModelsResult(success=False, message=_clean_http_error(e))
    except Exception as e:  # noqa: BLE001 —— 网络层什么都可能抛，照直转述给用户
        return ModelsResult(success=False, message=str(e))

    models = _model_ids(payload)
    if not models:
        return ModelsResult(
            success=False, message="上游返回了空清单，模型名请手动填写。",
        )
    return ModelsResult(success=True, models=models, message=f"找到 {len(models)} 个模型")


def _model_ids(payload: object) -> list[str]:
    """从上游响应里挑出模型 id。

    OpenAI 与 Anthropic 都回 ``{"data": [{"id": …}]}``；有些中转站直接回一个数组，
    元素可能是对象也可能就是字符串。全都认下来——这里宽容一点，总好过让用户对着
    「空清单」猜是谁的问题。
    """
    rows = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []
    ids = []
    for row in rows:
        name = row if isinstance(row, str) else (row.get("id") if isinstance(row, dict) else None)
        if isinstance(name, str) and name.strip():
            ids.append(name.strip())
    return sorted(set(ids))


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
    profiles = profile_store._load_image_profiles()
    for p in profiles:
        p.api_key = _mask_key(p.api_key)
    return profiles


@router.post("/ai/image-profiles", response_model=ImageProfile)
def create_image_profile(body: ImageProfileCreate):
    profiles = profile_store._load_image_profiles()
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
    profile_store._save_image_profiles(profiles)
    created.api_key = _mask_key(created.api_key)
    return created


@router.put("/ai/image-profiles/{profile_id}", response_model=ImageProfile)
def update_image_profile(profile_id: str, body: ImageProfileUpdate):
    profiles = profile_store._load_image_profiles()
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
    profile_store._save_image_profiles(profiles)
    target.api_key = _mask_key(target.api_key)
    return target


@router.delete("/ai/image-profiles/{profile_id}")
def delete_image_profile(profile_id: str):
    profiles = profile_store._load_image_profiles()
    remaining = [p for p in profiles if p.id != profile_id]
    if len(remaining) == len(profiles):
        raise HTTPException(status_code=404, detail="生图配置不存在")
    # 删掉的是激活项就顺位激活第一条；一条不剩 = 本机不出图（合法状态，配图静默跳过）
    if remaining and not any(p.is_active for p in remaining):
        remaining[0].is_active = True
    profile_store._save_image_profiles(remaining)
    return {"status": "ok"}


@router.post("/ai/image-profiles/{profile_id}/activate")
def activate_image_profile(profile_id: str):
    profiles = profile_store._load_image_profiles()
    if not any(p.id == profile_id for p in profiles):
        raise HTTPException(status_code=404, detail="生图配置不存在")
    for p in profiles:
        p.is_active = p.id == profile_id
    profile_store._save_image_profiles(profiles)
    return {"status": "ok"}


@router.get("/ai/image-profiles/{profile_id}/key")
def reveal_image_profile_key(profile_id: str):
    """返回该生图配置的完整 API Key（明文），供设置页「显示/复制」用。与文本配置同理。"""
    for p in profile_store._load_image_profiles():
        if p.id == profile_id:
            return {"api_key": p.api_key}
    raise HTTPException(status_code=404, detail="生图配置不存在")


@router.post("/ai/image-profiles/{profile_id}/test", response_model=TestResult)
async def test_image_profile(profile_id: str):
    """真出一张图验证该生图配置——用的是运行时同一条装配路径（image_generator_from_profile），
    所以「测试通过」意味着跑团时也确实出得来图。"""
    from app.ai.image_gen import image_generator_from_profile

    target = next((p for p in profile_store._load_image_profiles() if p.id == profile_id), None)
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
