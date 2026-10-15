"""终检误判收敛的回归测试（2026-10-15 实录）。

1. 第 4 条（代演）只拦「没宣言过的有后果的事」：执行宣言、写一眼可见的环境不算；
2. 旁白里漏出来的工具名确定性剥掉，不交给 LLM 整段改写；
3. 改写时原文与改写稿留底，便于事后统计误判。
"""

import asyncio
import json
import logging

from app.ai import turn_validator
from app.ai.turn_planner import TurnPlan
from app.services import chat_event_writer
from app.services.turn_context import _validate_only


def _prompt(**kw) -> str:
    msgs = turn_validator.build_validator_messages(TurnPlan(), "烛火斜了斜。", **kw)
    return "\n".join(m["content"] for m in msgs)


def test_执行已宣言的动作与一眼可见的环境写进了豁免():
    """实录：玩家宣言「朝着沙响的方向摸索」，旁白写他挪过去、往里走了几步，被判成代演；
    写「缺口下积着一小堆沙」被判成没检定的观察。两者都是 KP 的本职。"""
    joined = _prompt(turn_inputs="[阿尔文 行动] 我朝着沙响的方向摸索")
    assert "执行本轮已宣言的动作不算" in joined
    assert "朝沙响的方向摸索" in joined
    assert "场景里一眼可见的东西不算「补观察」" in joined
    # 真正该拦的仍在：没宣言的有后果动作、要靠检定才能发现的隐藏信息
    assert "没宣言过的、有后果的事" in joined
    assert "要靠技能检定才可能发现" in joined


def test_改写指令要求只动违规句子():
    joined = _prompt()
    assert "改写只动违规的那几句" in joined
    assert "拿不准算不算违规的，按不违规处理" in joined


def test_剥掉旁白里的工具名残留并修正对话偏移():
    narr = "风声听不真切。\n\ndice_check(skill=聆听)\n沙响断断续续。dice_check"
    marks = [(len(narr), "哈桑", "别动。")]
    result = [narr, narr, [], marks, []]
    chat_event_writer.strip_leaked_tool_names(result)
    assert result[0] == "风声听不真切。\n\n沙响断断续续。"
    assert result[3] == [(len(result[0]), "哈桑", "别动。")]
    assert result[1] == narr  # 指令解析用的原文不动


def test_普通英文词和形似的词不误剥():
    for text in ("他喊了一声 say。", "桌上压着一份 handout。", "check_dice 不是工具名。"):
        assert chat_event_writer.LEAKED_TOOL_NAME_RE.sub("", text) == text


def test_改写时原文与改写稿留底(caplog):
    class _LLM:
        async def complete(self, messages, **kw):
            return json.dumps({
                "violated": True, "reason": "第2条", "corrected_narration": "烛火斜了一下。",
            }, ensure_ascii=False)

    result = ["烛火斜了斜，flag_tomb_open 已触发。", "", [], [], []]
    with caplog.at_level(logging.INFO, logger="app.services.turn_context"):
        asyncio.run(_validate_only(_LLM(), TurnPlan(), result))
    assert result[0] == "烛火斜了一下。"
    rec = next(r for r in caplog.records if r.getMessage().startswith("终检改写对照|"))
    payload = json.loads(rec.getMessage().split("|", 1)[1])
    assert payload == {
        "reason": "第2条",
        "original": "烛火斜了斜，flag_tomb_open 已触发。",
        "corrected": "烛火斜了一下。",
    }
