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
