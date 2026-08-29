"""KP 流式叙事协议：把模型的 token 流切成「旁白 / NPC 台词气泡」。

本模块现在只是**驱动壳**，真正的两件事各自成模块：

- :mod:`app.services.narration_scanner` —— 字符级状态机（引号 / 方括号 / [SAY] /
  后置说话人四态嵌套），负责切分、剔除指令标签、产出 chunk；
- :mod:`app.services.narration_speakers` —— 「这句引号是谁说的」的全部启发式。

拆分前这三件事挤在一个 500 余行、圈复杂度上百的函数里。行为未变，
由 ``tests/test_narration_protocol_golden.py`` 的金标准快照与分词不变性钉住。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.services.narration_scanner import NarrationScanner, looks_like_speech
from app.services.narration_speakers import (
    is_party_speaker,
    narr_quote_span,
    strip_speaker_prefix,
)

# 兼容别名：外部调用方（kp_tool_loop / team_turn_service / 测试）沿用的旧私名。
_is_party_speaker = is_party_speaker
_strip_speaker_prefix = strip_speaker_prefix
_narr_quote_span = narr_quote_span
_looks_like_speech = looks_like_speech

__all__ = [
    "filter_narration_stream",
    "is_party_speaker",
    "narr_quote_span",
    "strip_speaker_prefix",
]


async def filter_narration_stream(
    token_stream: AsyncIterator[str], result: list,
    npcs: list[dict] | None = None,
    group_label: str | None = None,
    guess_speakers: bool = True,
    party_names: set[str] | None = None,
    shown_dialogues: list[str] | None = None,
    prior_narration: str = "",
) -> AsyncIterator[str]:
    """流式输出 KP 旁白，并把 NPC 台词抽成对话气泡。

    ``shown_dialogues`` 是本轮已经在界面显示的玩家/队友台词。裸引号若与其相同或高度相似，
    直接丢弃，避免 KP 把玩家气泡再次写进旁白。``prior_narration`` 用于工具续接跨步骤去重。

    ``party_names``（玩家 + AI 队友名）给定时，任何归到玩家党名下的台词都**不生成气泡**——
    KP 绝不能替玩家/队友发声。这是显式 [SAY] 与后置说话人路径缺失的守卫（裸引号路径本就避让玩家党）。

    ``guess_speakers=False``（结构化/say 工具路径）：**关闭裸引号的启发式说话人猜测**——
    无 [SAY] 标记的引号一律留旁白，绝不猜。对话由 say() 工具承担干净的结构化出口，
    故这里不再猜，从根上消灭「归错人」。[SAY] 显式标记仍照常识别（确定性、无歧义）。

    直接消费一个 token 流（与生成来源解耦）：旧路径喂 KPAgent.narrate 的输出，
    agent loop 路径喂 stream_chat 的文本增量——两条路径共用同一套台词抽取/
    指令剔除/流式分段逻辑。

    台词识别两条路：
    1. 显式 ``[SAY: who=<名字>]台词[/SAY]``（最可靠，用于消歧/无名角色/代词承接的说话人）。
    2. 自然引号台词（“”/「」）：据上下文判定说话人——书写/标识/被提及语境一律留旁白；
       否则按「紧邻说话前缀 → 当前说话人(承接) → 最近作为主语行动的 NPC」归属，
       都判不出且附近只有玩家名时，留旁白（不瞎猜）。

    命令标签（[DICE_CHECK] 等）仍终止本次流；[MOVE]/[GROUP] 内联剔除不终止。
    *result* = [narration, full_response, extracted, dialogue_marks, group_marks]。

    ``group_label`` 给定时（分头行动后端按组生成）：本次整段产物确定性地归入该组——
    既给流式 chunk 打上 ``metadata.group``（前端实时分栏），也以 ``(0, label)`` 预置
    group_mark（落库分组），不再依赖模型自觉打 [GROUP]。
    """
    scanner = NarrationScanner(
        result, npcs=npcs, group_label=group_label, guess_speakers=guess_speakers,
        party_names=party_names, shown_dialogues=shown_dialogues,
        prior_narration=prior_narration,
    )
    full_response = ""

    async for token in token_stream:
        # 注意：整个 token 先记进 full_response，再逐字符喂。命令标签终止时，标签之后、
        # 同一 token 之内的文字会残留在 full_response 里（已知缺口，见金标准测试）。
        full_response += token

        for ch in token:
            for chunk in scanner.feed(ch):
                yield chunk
            if scanner.terminated:
                break

        if scanner.terminated:
            for chunk in scanner.flush_on_terminate():
                yield chunk
            break

        # token 边界：把已成段或过长的旁白切出去，让前端尽早看到字
        for chunk in scanner.flush_boundaries():
            yield chunk

    if not scanner.terminated:
        for chunk in scanner.finish():
            yield chunk

    scanner.write_result(full_response)
