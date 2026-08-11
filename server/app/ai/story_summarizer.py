from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# 项目约定：一切 LLM 调用不设 max_tokens（输出长短交给 prompt 约束与服务端默认）。
# 摘要的「不膨胀」由提示词的浓缩要求保证，不靠输出预算硬砍。


def _events_text(events: list[Any]) -> str:
    """把一批事件渲染成给「剧情书记员」浓缩用的纯文本（客观、去格式）。"""
    lines: list[str] = []
    for ev in events:
        etype = getattr(ev, "event_type", "") or ""
        who = getattr(ev, "actor_name", "") or ""
        content = (getattr(ev, "content", "") or "").replace("\n", " ").strip()
        if not content:
            continue
        if etype == "narration":
            lines.append(f"旁白：{content}")
        elif etype == "dialogue":
            lines.append(f"{who or 'NPC'}：{content}")
        elif etype == "action":
            lines.append(f"{who or '某人'}（行动）：{content}")
        elif etype == "dice":
            lines.append(f"检定：{content}")
        elif etype == "system":
            lines.append(f"系统：{content}")
    return "\n".join(lines)


def build_memory_keeper_messages(
    prev_summary: str, events: list[Any], npc_memory_brief: str,
    team_memory_brief: str = "",
) -> list[dict]:
    """合并调用（v2）：一次低温 json_object 调用同时产出滚动摘要 + MemoryKeeper 差量。

    在同一批输入事件与既往摘要之上，额外喂入当前 npc_memory 摘要，让抽取器据
    「本轮新事件里 NPC 的言行变化」输出态度/承诺/谎言的差量。
    ``team_memory_brief`` 非空时（会话有 AI 队友）再抽取队友私有记忆差量（个人目标/心事）。
    输出严格 JSON：{summary, npc_updates, clue_notes[, team_updates]}。
    """
    body = _events_text(events)
    prev = (prev_summary or "").strip() or "（暂无既往摘要）"
    mem = (npc_memory_brief or "").strip() or "（暂无 NPC 记忆）"
    team = (team_memory_brief or "").strip()
    team_task = "" if not team else (
        "【team_updates 要求】对象，key 只能是下面队友记忆里**已列出的队友 id**，value 形如："
        '{"new_goals": ["该角色本段新产生的个人目标"], '
        '"done_goals": ["已完成/已放弃的目标原文"], '
        '"new_notes": ["该角色个人在意并会记住的事"]}。'
        "目标/心事须是**该角色自己的个人视角**（还某人的人情、查明某桩私事、护住某人、"
        "对某 NPC 起了疑心），不是全队共同的剧情待办（那归 summary 管）。"
        "仅当本段事件确有依据时才写，没有就省略该队友或留空。绝不臆造。\n"
    )
    team_section = "" if not team else f"【当前队友私有记忆】\n{team}\n\n"
    team_key = "" if not team else ', "team_updates": {}'
    return [
        {
            "role": "system",
            "content": (
                "你身兼 TRPG 剧情书记员与世界记忆守护者。基于既往摘要、本轮新事件与当前记忆，"
                "同时完成两件事，**只输出一个 JSON 对象**（不要解释、不要 markdown 围栏）：\n"
                "1. summary：把**本轮新事件**浓缩成一段连贯、客观的剧情梗概正文——"
                "这是接在既往梗概后面的新一段，不要把既往内容并进来。\n"
                "2. 其余字段：从新事件里抽取各类记忆的**差量**。"
            ),
        },
        {
            "role": "user",
            "content": (
                "【summary 要求】\n"
                "- **只写这一段新事件**：既往梗概仅供你理解前情与人物指代，既不要复述它，"
                "也不要把它的内容并进产出——系统会按时间顺序把各段拼成完整梗概。\n"
                "- 保留对后续推进重要的事实：去过哪些地点、见过哪些 NPC、已揭示的线索与结论、"
                "已做出的关键决定与承诺、尚未了结的悬念/待办、当前处境。\n"
                "- 舍弃寒暄与重复的氛围描写；按时间顺序、紧凑成段。\n"
                "- 线索与 NPC 关系已有专门台账维护，摘要侧重剧情脉络与因果，不必逐条保留线索细节。\n\n"
                "【npc_updates 要求】对象，key 只能是下面 NPC 记忆里**已列出的 NPC id**"
                "（不得凭空新增 NPC），value 形如："
                '{"attitude": "hostile|wary|neutral|warming|trusting", '
                '"attitude_reason": "一句话原因", "new_promises": ["本轮新许下的承诺"], '
                '"new_lies": ["本轮新说的谎"]}。仅当本轮事件确有变化时才写对应字段，'
                "没有变化就省略该 NPC 或留空。绝不臆造。\n"
                "【clue_notes 要求】对象，key 是线索 id，value 是一句话备注，"
                "**只用于给已存在的线索补充观察备注，绝不涉及玩家是否已掌握**。无则空对象。\n"
                + team_task
                + f"\n【既往剧情摘要】\n{prev}\n\n【当前 NPC 记忆】\n{mem}\n\n"
                + team_section
                + f"【本轮新发生的事件】\n{body}\n\n"
                + '现在输出 JSON：{"summary": "...", "npc_updates": {}, "clue_notes": {}'
                + team_key + "}"
            ),
        },
    ]


def _extract_json_object(raw: Any) -> dict | None:
    """从 LLM 原始输出稳健抠出 JSON object（容忍 dict / 代码围栏 / 前后夹带文字）。"""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None
    return None


async def summarize_and_extract(
    llm: Any, prev_summary: str, events: list[Any], npc_memory_brief: str,
    team_memory_brief: str = "",
) -> tuple[str, dict, dict, dict] | None:
    """合并调用：一次 complete() 同时拿到滚动摘要与 MemoryKeeper 差量。

    返回 ``(summary, npc_updates, clue_notes, team_updates)``；任何失败（无 LLM / 无事件 /
    调用异常 / 坏 JSON / 摘要为空）一律返回 None，由调用方保持原摘要与各记忆完全不变，
    绝不阻塞跑团。抽取的差量结构未经安全校验——落库前须经 ``world_memory.apply_memory_delta``
    / ``world_memory.apply_team_memory_delta``。
    """
    if llm is None or not events:
        return None
    try:
        raw = await llm.complete(
            build_memory_keeper_messages(
                prev_summary, events, npc_memory_brief, team_memory_brief,
            ),
            temperature=0,
            response_format={"type": "json_object"},
        )
    except Exception:
        logger.exception("摘要+记忆抽取合并调用失败，保持原摘要与原记忆")
        return None
    data = _extract_json_object(raw)
    if not isinstance(data, dict):
        return None
    summary = str(data.get("summary") or "").strip()
    if not summary:
        return None
    npc_updates = data.get("npc_updates")
    clue_notes = data.get("clue_notes")
    team_updates = data.get("team_updates")
    return (
        summary,
        npc_updates if isinstance(npc_updates, dict) else {},
        clue_notes if isinstance(clue_notes, dict) else {},
        team_updates if isinstance(team_updates, dict) else {},
    )


def build_merge_messages(chapters: list[str]) -> list[dict]:
    """二次合并：把最老的几章压成一章（分层摘要的下沉步骤）。

    只有这一步会让同一段剧情被压第二次，所以提示词要求偏向**保住事实骨架**而非文采——
    这些老章节离当前剧情最远、最可能被彻底遗忘，压坏了就只能靠 recall_history 捞原文。
    """
    body = "\n\n".join(f"【第 {i} 段】{c}" for i, c in enumerate(chapters, start=1))
    return [
        {
            "role": "system",
            "content": (
                "你是 TRPG 剧情书记员。把下面几段**早期**剧情梗概合并浓缩成一段连贯、客观的"
                "梗概。只输出正文，不要解释、不要标题、不要分段编号。"
            ),
        },
        {
            "role": "user",
            "content": (
                "要求：\n"
                "- 这是全局最早的一段历史，之后不会再有机会补充——**宁可保守，不要漏掉事实**："
                "去过的地点、见过的 NPC、已揭示的结论、做出的承诺与决定、尚未了结的悬念。\n"
                "- 可以压缩措辞与重复的氛围描写，但不要为了简短而丢事实。\n"
                "- 按时间顺序，紧凑成段。\n\n"
                f"{body}\n\n请输出合并后的梗概："
            ),
        },
    ]


async def merge_chapters(llm: Any, chapters: list[str]) -> str | None:
    """把几段早期梗概合并成一段；任何失败返回 None（调用方保持章节原样）。"""
    if llm is None or not chapters:
        return None
    try:
        raw = await llm.complete(build_merge_messages(chapters), temperature=0.2)
    except Exception:
        logger.exception("章节二次合并失败，保持原样")
        return None
    text = (raw or "").strip() if isinstance(raw, str) else ""
    return text or None
