"""关闭模型思考：{"thinking": {"type": "disabled"}}。

官方文档口径：思考模式**默认打开**，且 effort 默认 high；reasoning_effort 只调强度
（low/high/max）、关不掉思考。实测 deepseek-v4-flash：默认思考 73~140 token，
下发 thinking=disabled 后恒为 0，而下发 reasoning_effort=low/minimal 反而涨到 391/437。

思考内容还会被 complete() 丢弃（只收 delta.content）——时间照花、产物照扔，所以
「能关掉」对跑团这种多环节串行的场景是决定性的。
"""

from app.ai.providers.openai_compat import OpenAICompatProvider


def _payload(**kw) -> dict:
    p = OpenAICompatProvider(model="deepseek-v4-flash", **kw)
    return p._apply_reasoning({"model": p.model, "temperature": 0.7})


def test_thinking_disabled_sends_switch():
    assert _payload(thinking_disabled=True)["thinking"] == {"type": "disabled"}


def test_default_sends_nothing():
    """不设就什么都不下发——保持对其他 OpenAI 兼容后端的兼容。"""
    out = _payload()
    assert "thinking" not in out and "reasoning_effort" not in out
    assert out["temperature"] == 0.7        # 没设推理档时 temperature 保留


def test_effort_alone_does_not_disable_thinking():
    """强度不是开关：只填 reasoning_effort 不该产出 thinking 字段。

    这正是那次「设了 low 反而更慢」的成因——用户以为在关思考，其实只是在调强度。
    """
    out = _payload(reasoning_effort="low")
    assert "thinking" not in out
    assert out["reasoning_effort"] == "low"
    assert "temperature" not in out         # 设了推理档就省略 temperature


def test_switch_and_effort_can_coexist():
    """两者互不排斥；关了思考后强度值下发与否都不影响结果，不必在 Provider 层做互斥。"""
    out = _payload(thinking_disabled=True, reasoning_effort="low")
    assert out["thinking"] == {"type": "disabled"} and out["reasoning_effort"] == "low"


def test_applied_on_every_call_path():
    """四条调用路径（complete / complete_vision / stream_chat / stream）都要经过 _apply_reasoning，
    否则会出现「planner 关了思考、KP 叙事没关」这种只改一半的怪状。"""
    import inspect

    src = inspect.getsource(OpenAICompatProvider)
    assert src.count("self._apply_reasoning(payload)") == 4


def test_视觉调用走流式且可覆盖超时():
    """complete_vision 此前是一次非流式 POST，同时踩两个坑：

    - 非流式的读超时覆盖**整个等待过程**（要等模型把几千 token 的 JSON 全生成完才有第一个
      字节），客户端 120s 必然先到——用户实测报的就是 ReadTimeout('')。
    - 长输出被中途掐断，正是当初把 complete() 改流式的原因，而视觉解析输出更长。
    """
    import asyncio
    import json

    from app.ai.providers.openai_compat import OpenAICompatProvider

    seen = {}

    class _Resp:
        status_code = 200

        async def aiter_lines(self):
            for piece in ("{\"title\":", "\"某模组\"}"):
                yield "data: " + json.dumps({"choices": [{"delta": {"content": piece}}]})
            yield "data: [DONE]"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Client:
        def stream(self, method, url, **kw):
            seen["method"] = method
            seen["stream_flag"] = kw["json"].get("stream")
            seen["timeout"] = kw.get("timeout")
            return _Resp()

    p = OpenAICompatProvider(model="qwen3.7-plus", base_url="http://x", api_key="k", vision=True)
    p._client = _Client()
    out = asyncio.run(p.complete_vision("认字", [("YmFzZTY0", "image/png")], timeout=600))

    assert out == '{"title":"某模组"}'          # 分块拼回完整产物
    assert seen["stream_flag"] is True          # 确实走了流式
    assert seen["timeout"] == 600               # 按调用覆盖超时
