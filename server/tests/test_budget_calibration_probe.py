"""工具循环的校准取样：只取第一步的服务端 usage（口径＝调用方 messages + tools）。"""

from __future__ import annotations

import asyncio

from app.services import chat_service
from tests.test_kp_tool_loop import _call, _FakeToolLLM, _RecordingExecutor, _text


class _UsageLLM(_FakeToolLLM):
    """每步报一份递增的 usage（模拟工具往返让后续步的输入越来越长）。"""

    async def stream_chat(self, messages, tools=None, temperature=0.7, max_tokens=None):
        async for d in super().stream_chat(messages, tools=tools, temperature=temperature):
            yield d
        self.last_usage = {"prompt_tokens": 1000 * len(self.calls)}


class _SilentLLM(_FakeToolLLM):
    """不报 usage 的 Provider：last_usage 一直是更早那次调用留下的。"""

    last_usage = {"prompt_tokens": 42}


def _run(llm, probe):
    async def _go():
        result = ["", "", [], [], []]
        async for _ in chat_service._run_kp_agent_loop(
            llm, [{"role": "system", "content": "KP"}], result, _RecordingExecutor(),
            usage_probe=probe,
        ):
            pass
    asyncio.run(_go())


def test_probe_takes_first_step_usage_not_last():
    llm = _UsageLLM([[_call("set_mood", {"mood": "tense"})], [_text("夜色沉了下来。")]])
    probe: dict = {}
    _run(llm, probe)
    assert len(llm.calls) == 2
    assert probe["first"] == {"prompt_tokens": 1000}
    assert llm.last_usage == {"prompt_tokens": 2000}


def test_probe_ignores_stale_usage_from_earlier_call():
    llm = _SilentLLM([[_text("一夜无事。")]])
    probe: dict = {}
    _run(llm, probe)
    assert "first" not in probe
