"""测试辅助：把一段含方括号指令的 KP 文本，按生产路径（文本指令适配层的解析器 + 工具循环执行器）执行。

旧的 ``_process_commands`` 已删除；不支持原生工具的模型写出的文本指令，生产中由
``app.ai.text_tool_adapter`` 解析成工具调用、交 ``_build_kp_tool_executor`` 执行——这里复用
同样两段代码，只省掉模型往返。续写（自动掷骰后、查阅后）属于工具循环本身，见 test_kp_tool_loop。
"""
from __future__ import annotations

from app.services import kp_tool_loop
from app.services.turn_pipeline import NarrationResult


async def run_text_commands(
    db, session_id, text, module, player_char, game_session, llm=None, *,
    teammates=None, pre_gen_seq=None, dice_scope=None, result=None,
):
    execute = kp_tool_loop._build_kp_tool_executor(
        db, session_id, game_session, module, player_char, teammates, llm,
        result if result is not None else NarrationResult(),
        pre_gen_seq=pre_gen_seq, dice_scope=dice_scope,
    )
    for call in kp_tool_loop._tool_calls_from_text(text):
        outcome = await execute(call)
        for chunk in outcome.chunks:
            yield chunk


def _strip_commands(text: str) -> str:
    import re
    return re.sub(r"\[[A-Z_]{3,}(?:[:：\s][^\]]*)?\]", "", text or "").strip()


def scripted_kp_loop(*outputs, calls: list | None = None):
    """替身 ``_run_kp_agent_loop``：按顺序吐出预置的 KP 输出（每次调用一段），
    其中的方括号指令经适配层同款解析器转成工具调用、交真实执行器执行——等价于
    「纯文本模型 + 文本指令适配层」走一遍工具循环，只是省掉模型往返与续写轮。

    ``calls``：给定时记录每次调用收到的 messages。
    """
    from app.services.event_protocol import make_chunk

    queue = list(outputs)

    async def fake(llm, messages, result, execute, **kw):
        if calls is not None:
            calls.append(messages)
        text = queue.pop(0) if queue else ""
        narration = _strip_commands(text)
        result[0] += narration
        result[1] += text
        if narration:
            yield make_chunk("narration", narration)
        for call in kp_tool_loop._tool_calls_from_text(text):
            outcome = await execute(call)
            for chunk in outcome.chunks:
                yield chunk
            if outcome.suspend:
                return

    return fake


def loop_from_stream_fake(stream_fake):
    """把旧测试里替身 ``_stream_narration_filtered(kp, messages, result, **kw)`` 的写法
    包成 ``_run_kp_agent_loop`` 替身：先让旧替身写 result，再按适配层口径执行其中的指令。"""
    from app.services.event_protocol import make_chunk

    async def fake(llm, messages, result, execute, **kw):
        # 直接写调用方的 result：旧替身中途抛错时，半截产物照样由流水线落库。
        async for chunk in stream_fake(None, messages, result, **{
            k: v for k, v in kw.items() if k in ("npcs", "group_label", "party_names")
        }):
            yield chunk
        if result[0]:
            yield make_chunk("narration", result[0])
        for call in kp_tool_loop._tool_calls_from_text(result[1]):
            outcome = await execute(call)
            for chunk in outcome.chunks:
                yield chunk
            if outcome.suspend:
                return

    return fake
