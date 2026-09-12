"""Provider 层工具调用扩展的单测：流式分片聚合、协议翻译、默认降级。

不发真实 HTTP——聚合器与翻译函数都是纯逻辑。
"""

import asyncio

import pytest

from app.ai.provider import LLMProvider, StreamDelta, ToolCall
from app.ai.providers.anthropic import messages_to_anthropic, tools_to_anthropic
from app.ai.providers.openai_compat import ToolCallAggregator


# ── OpenAI 流式分片聚合 ──


class TestToolCallAggregator:
    def test_分片参数聚合为完整调用(self):
        agg = ToolCallAggregator()
        agg.add([{"index": 0, "id": "call_1",
                  "function": {"name": "dice_check", "arguments": ""}}])
        agg.add([{"index": 0, "function": {"arguments": '{"skill"'}}])
        agg.add([{"index": 0, "function": {"arguments": ': "侦查"}'}}])
        calls = agg.flush()
        assert calls == [ToolCall(id="call_1", name="dice_check",
                                  arguments={"skill": "侦查"})]

    def test_多个并发调用按index分开(self):
        agg = ToolCallAggregator()
        agg.add([
            {"index": 0, "id": "a", "function": {"name": "set_flag", "arguments": '{"flag": "f1"}'}},
            {"index": 1, "id": "b", "function": {"name": "move", "arguments": '{"actor": "x"}'}},
        ])
        calls = agg.flush()
        assert [c.name for c in calls] == ["set_flag", "move"]

    def test_坏JSON参数归一为空dict(self):
        agg = ToolCallAggregator()
        agg.add([{"index": 0, "id": "a",
                  "function": {"name": "dice_check", "arguments": '{"skill": 断'}}])
        calls = agg.flush()
        assert calls[0].arguments == {}

    def test_无参数调用(self):
        agg = ToolCallAggregator()
        agg.add([{"index": 0, "id": "a", "function": {"name": "noop", "arguments": ""}}])
        assert agg.flush()[0].arguments == {}

    def test_flush后清空_二次flush为空(self):
        agg = ToolCallAggregator()
        agg.add([{"index": 0, "id": "a", "function": {"name": "n", "arguments": "{}"}}])
        assert agg.flush()
        assert agg.flush() == []

    def test_无名坏流丢弃(self):
        agg = ToolCallAggregator()
        agg.add([{"index": 0, "function": {"arguments": "{}"}}])
        assert agg.flush() == []


# ── Anthropic 协议翻译 ──


class TestAnthropicTranslation:
    def test_工具schema翻译(self):
        tools = [{"type": "function", "function": {
            "name": "dice_check", "description": "掷骰",
            "parameters": {"type": "object", "properties": {"skill": {"type": "string"}}},
        }}]
        out = tools_to_anthropic(tools)
        assert out[0]["name"] == "dice_check"
        assert out[0]["input_schema"]["properties"]["skill"]["type"] == "string"

    def test_assistant带tool_calls翻译为tool_use块(self):
        messages = [{
            "role": "assistant", "content": "让我掷个骰。",
            "tool_calls": [{"id": "call_1", "type": "function",
                            "function": {"name": "dice_check",
                                         "arguments": '{"skill": "侦查"}'}}],
        }]
        out = messages_to_anthropic(messages)
        blocks = out[0]["content"]
        assert blocks[0] == {"type": "text", "text": "让我掷个骰。"}
        assert blocks[1]["type"] == "tool_use"
        assert blocks[1]["input"] == {"skill": "侦查"}

    def test_tool结果翻译为tool_result块(self):
        messages = [{"role": "tool", "tool_call_id": "call_1", "content": "掷出 42，成功"}]
        out = messages_to_anthropic(messages)
        assert out[0]["role"] == "user"
        block = out[0]["content"][0]
        assert block["type"] == "tool_result"
        assert block["tool_use_id"] == "call_1"
        assert "42" in block["content"]

    def test_普通消息透传(self):
        messages = [{"role": "user", "content": "你好"}]
        assert messages_to_anthropic(messages) == messages


# ── 抽象层默认行为 ──


class _PlainProvider(LLMProvider):
    """不支持工具的最小 Provider，用于验证默认降级路径。"""

    async def complete(self, messages, temperature=0.7, max_tokens=None,
                       response_format=None):
        return "ok"

    async def stream(self, messages, temperature=0.7, max_tokens=None):
        yield "一段"
        yield "文本"


async def _collect(provider: LLMProvider, **kwargs) -> list[StreamDelta]:
    return [d async for d in provider.stream_chat([{"role": "user", "content": "hi"}], **kwargs)]


class TestDefaultStreamChat:
    def test_默认不支持工具(self):
        assert not _PlainProvider().supports_tools()

    def test_无tools时退化为文本流(self):
        deltas = asyncio.run(_collect(_PlainProvider()))
        assert all(isinstance(d, StreamDelta) and d.kind == "text" for d in deltas)
        assert "".join(d.text for d in deltas) == "一段文本"

    def test_带tools调用不支持的Provider报错(self):
        with pytest.raises(NotImplementedError):
            asyncio.run(_collect(
                _PlainProvider(),
                tools=[{"type": "function", "function": {"name": "x"}}],
            ))


# ── 两种 tool_calls 流式协议（Gemini 实抓踩出来的） ─────────────────────────

def _agg(*parts_per_chunk):
    from app.ai.providers.openai_compat import ToolCallAggregator
    a = ToolCallAggregator()
    for parts in parts_per_chunk:
        a.add(parts)
    return a.flush()


def test_openai_式分片按_index_增量拼接():
    """首片给 id 与 name，后续片只给同一 index 下的 arguments 增量。"""
    calls = _agg(
        [{"index": 0, "id": "c0", "type": "function", "function": {"name": "say", "arguments": '{"who":'}}],
        [{"index": 0, "function": {"arguments": '"诺特"}'}}],
    )
    assert len(calls) == 1
    assert calls[0].id == "c0" and calls[0].name == "say" and calls[0].arguments == {"who": "诺特"}


def test_没有_index_时每个带名字的分片都是独立调用():
    """Gemini 的兼容层不给 index，每片就是一次完整调用。
    此前一律归到 0 号槽 → arguments 拼成 {"who":…}{"scene_id":…} 解析失败、
    名字被后者覆盖，回传给模型是个对不上号的缝合怪（实测每轮必 400）。"""
    calls = _agg([
        {"id": "c1", "type": "function", "function": {"name": "say", "arguments": '{"who":"诺特"}'}},
        {"id": "c2", "type": "function", "function": {"name": "travel_suggest", "arguments": '{"scene_id":"street"}'}},
    ])
    assert [(c.id, c.name, c.arguments) for c in calls] == [
        ("c1", "say", {"who": "诺特"}),
        ("c2", "travel_suggest", {"scene_id": "street"}),
    ]


def test_没有_index_的无名分片续上一次调用():
    """万一将来它也改成增量下发：只带 arguments、不带 name 的分片要续到最后一个槽。"""
    calls = _agg(
        [{"id": "c1", "function": {"name": "say", "arguments": '{"who":'}}],
        [{"function": {"arguments": '"诺特"}'}}],
    )
    assert len(calls) == 1 and calls[0].arguments == {"who": "诺特"}


def test_思考签名被保留并只挂在它原本所在的调用上():
    """Gemini 把 thought_signature 挂在第一个 function call 的 extra_content 上。"""
    sig = {"google": {"thought_signature": "EpoHCpcH…"}}
    calls = _agg([
        {"id": "c1", "extra_content": sig, "function": {"name": "say", "arguments": "{}"}},
        {"id": "c2", "function": {"name": "travel_suggest", "arguments": "{}"}},
    ])
    assert calls[0].extra_content == sig
    assert calls[1].extra_content == {}      # 本来就没有，不凭空造


def test_不下发该字段的后端不受影响():
    calls = _agg([{"index": 0, "id": "c0", "function": {"name": "say", "arguments": "{}"}}])
    assert calls[0].extra_content == {}
