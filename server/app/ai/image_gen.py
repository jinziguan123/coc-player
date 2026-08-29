"""文生图后端：与文本 Provider 完全解耦，由独立的生图配置装配。

从前生图寄生在文本 Provider 上（`OpenAICompatProvider` 兼职 images 端点、ComfyUI 靠
`set_comfyui` 挂到任意 Provider 上），代价是：用 Anthropic 跑团就没法用 OpenAI 生图——
`image_model` 压根不会传给 AnthropicProvider，填了也**静默失效**。

现在生图是自己的一条链：``get_image_llm()`` 按激活的 ImageProfile 造一个 ImageGenerator，
跟文本模型用谁、走什么协议毫无关系。

对外仍暴露 ``supports_image_gen()`` / ``generate_image()`` 两个方法，与旧 Provider 同名——
调用点只换取用对象，不改调用形状。失败一律返回 None：配图是可选增强，绝不阻塞跑团。
"""

from __future__ import annotations

import base64
import logging

import httpx

from app.ai import profile_store

logger = logging.getLogger(__name__)


class ImageGenerator:
    """生图后端抽象。默认实现＝不出图（没配生图配置时的空对象）。"""

    def supports_image_gen(self) -> bool:
        return False

    async def generate_image(self, prompt: str, size: str = "1024x1024") -> str | None:
        """文生图，返回 base64（无 data: 前缀）；不支持或失败返回 None。"""
        return None


class NullImageGenerator(ImageGenerator):
    """没有激活的生图配置时使用——调用方据 supports_image_gen() 自行跳过配图。"""


class OpenAIImageGenerator(ImageGenerator):
    """OpenAI Images 端点（``{base}/images/generations``）。"""

    def __init__(self, model: str, base_url: str = "", api_key: str = ""):
        self._model = (model or "").strip()
        self._api_key = api_key or ""
        base = (base_url or "https://api.openai.com/v1").rstrip("/")
        self._url = f"{base}/images/generations"
        self._client = httpx.AsyncClient(timeout=180.0)   # 生图慢，超时给足

    def supports_image_gen(self) -> bool:
        return bool(self._model)

    async def generate_image(self, prompt: str, size: str = "1024x1024") -> str | None:
        """不下发 response_format 以兼容 dall-e-3（默认回 url）与 gpt-image-1（默认回 b64_json）：
        两种响应都能解析，回的是 url 时再抓一次转成 base64。"""
        if not self._model:
            return None
        payload = {"model": self._model, "prompt": prompt, "size": size, "n": 1}
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        try:
            resp = await self._client.post(self._url, headers=headers, json=payload)
            resp.raise_for_status()
            item = ((resp.json().get("data") or [{}])[0]) or {}
            if item.get("b64_json"):
                return item["b64_json"]
            if item.get("url"):
                img = await self._client.get(item["url"])
                img.raise_for_status()
                return base64.b64encode(img.content).decode()
        except Exception:
            logger.warning("文生图失败（忽略，不影响游戏）: model=%s", self._model, exc_info=True)
        return None


class ComfyUIImageGenerator(ImageGenerator):
    """内网 ComfyUI 工作流；实际时序与占位符替换都在 ComfyUIClient 里。"""

    def __init__(self, base_url: str, workflow: str = ""):
        from app.ai.comfyui import ComfyUIClient

        self._base_url = (base_url or "").strip()
        self._client = ComfyUIClient(self._base_url, workflow) if self._base_url else None

    def supports_image_gen(self) -> bool:
        return self._client is not None

    async def generate_image(self, prompt: str, size: str = "1024x1024") -> str | None:
        if self._client is None:
            return None
        return await self._client.generate(prompt)


def image_generator_from_profile(profile) -> ImageGenerator:
    """按生图配置装配后端（设置页「测试生图」与运行时共用，保证测的就是跑的）。"""
    if profile is None:
        return NullImageGenerator()
    if getattr(profile, "backend", "") == "comfyui":
        return ComfyUIImageGenerator(
            getattr(profile, "comfyui_base_url", ""),
            getattr(profile, "comfyui_workflow", ""),
        )
    return OpenAIImageGenerator(
        getattr(profile, "model", ""),
        getattr(profile, "base_url", ""),
        getattr(profile, "api_key", ""),
    )


def get_image_llm() -> ImageGenerator:
    """当前激活的生图后端；没配过生图则返回空对象（配图静默跳过，不报错、不中断游戏）。"""
    return image_generator_from_profile(profile_store.load_active_image_profile())
