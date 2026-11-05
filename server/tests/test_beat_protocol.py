"""BEAT 文本指令 → 工具调用的协议测试。"""

from app.services import command_protocol
from app.services.kp_tool_loop import _tool_calls_from_text


def test_beat_tag_is_recognized_as_command():
    assert command_protocol.is_command_tag("BEAT: ev_1")
    assert command_protocol.BEAT_RE.search("[BEAT: ev_1]").group(1) == "ev_1"


def test_beat_text_parses_to_tool_call():
    calls = _tool_calls_from_text("[BEAT: ev_1]")
    assert calls and calls[0].name == "beat"
    assert calls[0].arguments == {"id": "ev_1"}
