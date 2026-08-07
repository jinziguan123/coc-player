from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

# ── 上下文装配 → Provider 的进程内契约 ──────────────────────────────────────
# 上下文组装方（app.ai.context）可以在**开头的** system 消息上挂这个键，值是把该条系统
# 提示按「稳定性」切开的文本块列表（静态手册 → 半静态模组数据 → 每轮变的台账/记忆）。
# 支持 prompt caching 的 Provider 据此在稳定块末尾打缓存断点；不支持的直接读 content，
# 行为完全不变。
#
# **这不是 API 字段**：Provider 必须在构造请求体前用 strip_provider_keys 剔除，
# 否则会作为未知字段发给服务端（OpenAI 兼容端点会 400）。
CACHE_BLOCKS_KEY = "_cache_blocks"

# 仅供 Provider 内部消费、不得出现在请求体里的键（未来新增同类元数据一并加到这里）。
_PROVIDER_ONLY_KEYS = (CACHE_BLOCKS_KEY,)


def strip_provider_keys(messages: list[dict]) -> list[dict]:
    """剔除消息上的进程内元数据键，返回可直接序列化发给服务端的消息列表。

    只在确有该键时才复制 dict，正常路径零额外开销。
    """
    out = []
    for msg in messages:
        if any(k in msg for k in _PROVIDER_ONLY_KEYS):
            msg = {k: v for k, v in msg.items() if k not in _PROVIDER_ONLY_KEYS}
        out.append(msg)
    return out


@dataclass
class ToolCall:
    """一次完整的工具调用（流式聚合完成后才产出，arguments 已解析为 dict）。"""

    id: str
    name: str
    arguments: dict = field(default_factory=dict)


@dataclass
class StreamDelta:
    """stream_chat 的流式增量：文本片段或一次完整的工具调用。

    - kind="text"：text 为本次增量文本；
    - kind="reasoning"：text 为供应商要求在工具续接时原样回传的思考内容，不对玩家展示；
    - kind="tool_call"：tool_call 为聚合完成的调用（供应商的参数分片由 Provider 内部聚合，
      调用方永远拿到完整调用，不需要自己拼 JSON 片段）。
    """

    kind: str  # "text" | "reasoning" | "tool_call"
    text: str = ""
    tool_call: ToolCall | None = None


class LLMProvider(ABC):
    """LLM 服务提供者抽象接口"""

    # 最近一次调用的服务端真实 usage（{prompt_tokens, completion_tokens, total_tokens, ...}）；
    # 不支持的 Provider 保持 None，调用方回落启发式估算。
    last_usage: dict | None = None

    @abstractmethod
    async def complete(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        response_format: dict | None = None,
    ) -> str: ...

    @abstractmethod
    async def stream(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]: ...

    # ── 工具调用（function calling）：默认不支持，具备能力的 Provider 覆盖 ──
    # 消息与工具的**统一格式为 OpenAI 风格**（tools=function schema 列表；对话里 assistant
    # 消息可带 tool_calls、工具结果用 role="tool" + tool_call_id）——非 OpenAI 协议的
    # Provider 在自己内部翻译，调用方（agent loop）不感知协议差异。
    def supports_tools(self) -> bool:
        return False

    async def stream_chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamDelta]:
        """流式对话，支持工具调用。tools 为空时等价于 stream()（文本增量包装）。

        默认实现只处理无工具场景，保证所有 Provider 都能被 agent loop 统一调用；
        带 tools 调用一个不支持工具的 Provider 是编排层的 bug——用 supports_tools()
        先分流，而不是靠这里抛错兜底。
        """
        if tools:
            raise NotImplementedError("当前模型不支持工具调用")
        async for text in self.stream(messages, temperature=temperature, max_tokens=max_tokens):
            yield StreamDelta(kind="text", text=text)

    # ── 多模态（视觉）：默认不支持，视觉 Provider 覆盖 ──
    def supports_vision(self) -> bool:
        return False

    async def complete_vision(
        self, prompt: str, images: list[tuple[str, str]], max_tokens: int | None = None,
    ) -> str:
        """据若干图片 + 文本提示生成文本（多模态）。images=[(base64, mime), …]。非视觉 Provider 不实现。"""
        raise NotImplementedError("当前模型不支持多模态")

    # ── 文生图不在本抽象里 ──
    # 生图有自己的配置与后端链路，见 app.ai.image_gen 的 ImageGenerator / get_image_llm()。
    # 从前它挂在这里（set_comfyui + OpenAI images 兼职），导致「用 Anthropic 跑团就没法用
    # OpenAI 生图」这类静默失效；现在文本 Provider 只管文本。
