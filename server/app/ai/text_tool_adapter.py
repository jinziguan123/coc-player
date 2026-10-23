"""文本指令 → 工具调用的 Provider 适配层（草案 7.6 的落地形态）。

不支持原生工具调用的 Provider（或房主关了工具调用）只能把指令写成方括号文本。这里把它包成一个
「支持工具」的 Provider：照常流式吐文本，生成结束后把文本里的终止型指令解析成 ``tool_call``
增量；工具结果在下一轮请求里改写成普通消息回注。编排层因此只需工具循环一条路径。

解析器由服务层注入（``parse``），本模块不依赖服务层。不支持原生工具调用（或房主关了工具调用）的
配置一律经此适配；旧的文本指令路径（``_process_commands``）已在 2026-10-23 真实模型评测持平后删除。
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable

from app.ai.provider import LLMProvider, StreamDelta, ToolCall

#: 工具结果回注成普通消息时的前缀：让纯文本模型知道这是系统执行结果、不是玩家发言。
TOOL_RESULT_PREFIX = "【系统执行结果】"

#: 需要模型据结果继续叙述的工具类别（与旧文本路径一致：自动掷骰结果、查阅结果才续写）。
#: 场景切换、开战、标志等状态指令按旧约定是「收尾指令」，执行完本轮就结束——
#: 真实模型评测里，若状态指令后也续写，模型会在开战后再追加检定，越出裁定计划。
CONTINUE_KINDS = frozenset({"check", "lookup"})


def needs_continuation(messages: list[dict]) -> bool:
    """末尾是不是一轮「工具调用 + 结果」，且其中有需要据结果续写的工具。

    末尾不是工具结果（首轮请求）时返回 True：照常生成。
    """
    from app.ai import tools as kp_tools

    if not messages or messages[-1].get("role") != "tool":
        return True
    for message in reversed(messages):
        if message.get("role") == "tool":
            continue
        if message.get("role") == "assistant" and message.get("tool_calls"):
            names = [(tc.get("function") or {}).get("name") for tc in message["tool_calls"]]
            return any(
                (spec := kp_tools.TOOLS_BY_NAME.get(name)) is not None and spec.kind in CONTINUE_KINDS
                for name in names
            )
        return True
    return True


def to_text_messages(messages: list[dict]) -> list[dict]:
    """把工具循环的消息历史改写成纯文本模型能吃的形态。

    - assistant 的 tool_calls 去掉（指令原文已在 content 里，原生工具调用合成的则还原成标签不必要：
      纯文本模型只会看到自己写过的方括号指令）；
    - role="tool" 的结果改成 user 消息，带上前缀；
    - 其余消息原样保留（含 system）。
    """
    from app.ai import tools as kp_tools

    names: dict[str, str] = {}
    out: list[dict] = []
    for message in messages:
        role = message.get("role")
        if role == "tool":
            content = message.get("content") or ""
            spec = kp_tools.TOOLS_BY_NAME.get(names.get(message.get("tool_call_id") or "", ""))
            if spec is not None and spec.kind in CONTINUE_KINDS:
                # 检定/查阅的结果由执行器写成完整的续写提示（KP_DICE_/RULE_/MODULE_CONTINUATION_PROMPT，
                # 含叙述主语、不复述点数等约束），原样作为用户消息回注，不再加前缀或二次包装。
                out.append({"role": "user", "content": content})
            else:
                out.append({"role": "user", "content": f"{TOOL_RESULT_PREFIX}{content}"})
            continue
        for tc in message.get("tool_calls") or []:
            names[str(tc.get("id") or "")] = (tc.get("function") or {}).get("name") or ""
        if role == "assistant" and message.get("tool_calls"):
            content = message.get("content") or ""
            if not content.strip():
                # 没有正文的工具轮（理论上纯文本模型不会出现），留一行标签痕迹免得出现空 assistant
                content = "\n".join(
                    f"[{(tc.get('function') or {}).get('name', '').upper()}: "
                    f"{_args_text((tc.get('function') or {}).get('arguments'))}]"
                    for tc in message["tool_calls"]
                )
            out.append({"role": "assistant", "content": content})
            continue
        out.append({k: v for k, v in message.items() if k not in ("tool_calls", "reasoning_content")})
    return out


def _args_text(raw) -> str:
    try:
        args = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
    except (TypeError, ValueError):
        return str(raw or "")
    return ", ".join(f"{k}={v}" for k, v in args.items())


class TextToolAdapter(LLMProvider):
    """把纯文本 Provider 包成「支持工具」的 Provider；其余接口原样委托。"""

    text_commands = True   # 编排层据此不追加「工具调用模式」提示（那段话要求模型别写文本指令）

    def __init__(self, inner: LLMProvider, parse: Callable[[str], list[ToolCall]]):
        self.inner = inner
        self.parse = parse

    def __getattr__(self, name):
        # model、base_url、last_usage 等属性一律透传给被包装的 Provider
        return getattr(self.inner, name)

    @property
    def last_usage(self):   # 基类的类属性会挡住 __getattr__，显式透传
        return getattr(self.inner, "last_usage", None)

    def supports_tools(self) -> bool:
        return True

    async def complete(self, messages, temperature=0.7, max_tokens=None, response_format=None, timeout=None):
        return await self.inner.complete(
            messages, temperature=temperature, max_tokens=max_tokens,
            response_format=response_format, timeout=timeout,
        )

    async def stream(self, messages, temperature=0.7, max_tokens=None) -> AsyncIterator[str]:
        async for text in self.inner.stream(messages, temperature=temperature, max_tokens=max_tokens):
            yield text

    async def stream_chat(
        self, messages: list[dict], tools: list[dict] | None = None,
        temperature: float = 0.7, max_tokens: int | None = None,
    ) -> AsyncIterator[StreamDelta]:
        if not needs_continuation(messages):
            return   # 只执行了收尾型状态指令：本轮到此结束，不再请求模型
        produced: list[str] = []
        async for text in self.inner.stream(
            to_text_messages(messages), temperature=temperature, max_tokens=max_tokens,
        ):
            produced.append(text)
            yield StreamDelta(kind="text", text=text)
        if not tools:
            return   # 收尾轮（不许再调工具）：文本里残留的指令不执行
        for call in self.parse("".join(produced)):
            yield StreamDelta(kind="tool_call", tool_call=call)
