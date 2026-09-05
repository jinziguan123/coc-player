"""SSE 流「假活着」防护：服务端回了响应头只发心跳不发数据行，必须在有限时间内判定挂起。

复现场景（2026-09-05）：opencode.ai 的 zen 代理对 1.9 万字的 planner 请求回 200 后 5 分钟无正文，
httpx 读超时被心跳续命，planner 的 await 无限挂起，整局停在「KP 正在思考」。
"""

import asyncio
import json

import pytest

from app.ai import provider as provider_mod
from app.ai.provider import StreamStalled, iter_sse_data_lines
from app.ai.providers.openai_compat import OpenAICompatProvider


class _Resp:
    status_code = 200

    def __init__(self, lines, gap=0.0):
        self._lines, self._gap = lines, gap

    async def aiter_lines(self):
        for line in self._lines:
            if self._gap:
                await asyncio.sleep(self._gap)
            yield line

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def raise_for_status(self):
        pass


class _Client:
    def __init__(self, resp):
        self.resp, self.calls = resp, 0

    def stream(self, method, url, **kw):
        self.calls += 1
        return self.resp


def _data(piece):
    return "data: " + json.dumps({"choices": [{"delta": {"content": piece}}]})


def test_只有心跳不发数据行_判定挂起():
    async def main():
        resp = _Resp([": keep-alive"] * 50, gap=0.02)      # 1 秒的纯心跳
        out = []
        async for line in iter_sse_data_lines(resp, "http://x", stall_seconds=0.2):
            out.append(line)
    with pytest.raises(StreamStalled) as ei:
        asyncio.run(main())
    assert "http://x" in str(ei.value) and "心跳" in str(ei.value)


def test_心跳不续命_数据行才续命():
    """心跳夹在数据行之间无所谓；但一段连续心跳超过阈值就算挂起，不管之前多活跃。"""
    async def ok():
        lines = [_data("a"), ": hb", ": hb", _data("b"), ": hb", "data: [DONE]"]
        return [l async for l in iter_sse_data_lines(_Resp(lines, gap=0.03), stall_seconds=0.2)]
    got = asyncio.run(ok())
    assert len(got) == 3 and got[-1] == "data: [DONE]"      # 只产出数据行，心跳被吞

    async def stalled_after_activity():
        lines = [_data("a")] + [": hb"] * 30 + [_data("b")]
        return [l async for l in iter_sse_data_lines(_Resp(lines, gap=0.02), stall_seconds=0.2)]
    with pytest.raises(StreamStalled):
        asyncio.run(stalled_after_activity())


def test_complete_遇挂起立即抛_不重试(monkeypatch):
    """挂起不是瞬时抖动：重试只会再挂一次阈值那么久。抛出后由回合层收尾（错误提示 + done）。"""
    monkeypatch.setattr(provider_mod, "STREAM_STALL_SECONDS", 0.2)
    p = OpenAICompatProvider(model="m", base_url="http://x", api_key="k")
    client = _Client(_Resp([": keep-alive"] * 50, gap=0.02))
    p._client = client
    with pytest.raises(StreamStalled):
        asyncio.run(p.complete([{"role": "user", "content": "hi"}]))
    assert client.calls == 1


def test_正常流不受影响(monkeypatch):
    monkeypatch.setattr(provider_mod, "STREAM_STALL_SECONDS", 0.5)
    p = OpenAICompatProvider(model="m", base_url="http://x", api_key="k")
    p._client = _Client(_Resp([_data("你"), ": hb", _data("好"), "data: [DONE]"], gap=0.01))
    assert asyncio.run(p.complete([{"role": "user", "content": "hi"}])) == "你好"


def test_失败文案能认出挂起():
    from app.services.turn_orchestrator import _failure_notice
    assert "心跳" in _failure_notice(StreamStalled("x"))


def test_规划器超预算就回退旧流程(monkeypatch):
    """推理失控的模型会一直流 reasoning 不出正文（实测 200s 吐 3.1 万字、正文 0 字）。
    规划器是可选环节，超墙钟预算直接返回 None 走旧流程，不许把整局挂死。"""
    from app.ai import turn_planner

    monkeypatch.setattr(turn_planner, "PLANNER_TIMEOUT_SECONDS", 0.2)

    class _SlowLLM:
        async def complete(self, messages, temperature=0.7, max_tokens=None, response_format=None):
            await asyncio.sleep(5)
            return '{"requires_check": false}'

    import time
    t0 = time.monotonic()
    assert asyncio.run(turn_planner.run_turn_planner(_SlowLLM(), [{"role": "user", "content": "x"}])) is None
    assert time.monotonic() - t0 < 2


def test_规划器正常返回不受预算影响():
    from app.ai import turn_planner

    class _FastLLM:
        async def complete(self, messages, temperature=0.7, max_tokens=None, response_format=None):
            return '{"requires_check": false}'

    plan = asyncio.run(turn_planner.run_turn_planner(_FastLLM(), [{"role": "user", "content": "x"}]))
    assert plan is not None and plan.requires_check is False
