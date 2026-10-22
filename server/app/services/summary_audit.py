"""压缩后自检：每次滚动摘要 / 章节合并后，用外置台账对照新章节，确定性找出被漏掉的关键项。

摘要是有损的，且错误会复利：这一章漏了「找到了染血的日记本」，之后所有 KP 上下文里这件事
就不存在了，原文早已挤出窗口。台账（线索台账、NPC 互动史、剧情线台账、剧情标志）是逐事件
确定性记的，可以拿来对账：

- **线索**：本章覆盖的 seq 区间内进台账的线索，名字没出现在章节里 → 漏；
- **NPC**：本章区间内有互动 / 开过口的 NPC，名字没出现 → 漏；
- **承诺**：本批原文里带承诺/约定标记的句子，章节与剧情线台账里都找不到对应内容 → 漏；
- **剧情进度**：自上次自检以来新触发的剧情钩子，标题没出现 → 记日志。

补救策略（零 token，结果可预测）：
- 漏掉的线索 / NPC → 按原名在该章末尾补一行「台账补记」。只补玩家亲历过的东西（已发现的
  线索、见过的 NPC）：章节正文还会喂给收场后的角色小传，KP 专属的内容不能进来；
- 漏掉的承诺 → 原句补写进剧情线台账（标 ``source=audit``），之后每轮随台账注入 KP；
- 剧情进度只记日志不补写：标志本身就是外置的结构化状态，压缩丢不掉；钩子标题可能是
  守秘人用语，不宜写进玩家口吻的章节。

每次自检的结论写进 ``world_state.summary_audit``（最近一次）并打日志，便于观察漏检率。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.services import plot_progress, plot_threads, world_memory

logger = logging.getLogger(__name__)

AUDIT_KEY = "summary_audit"
MEMO_PREFIX = "（台账补记："
#: 一批事件里最多补记几条承诺：标记词是宽口径，防一段长对话刷出一串误报
MAX_PROMISES_PER_BATCH = 3
_PROMISE_RE = re.compile(
    r"答应|保证|承诺|约定|约好|说好|发誓|一言为定|立誓|许诺|守口如瓶|绝不告诉"
)
_SENTENCE_SPLIT = re.compile(r"[。！？!?\n]+")
_PUNCT = re.compile(r"[\s，,、；;：:“”\"'‘’（）()《》「」『』…—\-·.]+")
_PAREN = re.compile(r"[（(][^）)]*[）)]")
_TEXT_EVENT_TYPES = ("narration", "dialogue", "action")


def _name_variants(name: str) -> list[str]:
    """名字的可接受写法：原名、去括注、按间隔号/空格拆开的各段（≥2 字）。"""
    name = str(name or "").strip()
    if not name:
        return []
    out = [name]
    bare = _PAREN.sub("", name).strip()
    if bare and bare not in out:
        out.append(bare)
    for part in re.split(r"[·・\s]+", bare):
        if len(part) >= 2 and part not in out:
            out.append(part)
    return out


def mentioned(name: str, text: str) -> bool:
    return any(v in (text or "") for v in _name_variants(name))


def _in_range(seq, from_seq: int, to_seq: int) -> bool:
    try:
        return from_seq <= int(seq or 0) <= to_seq
    except (TypeError, ValueError):
        return False


def _clues_in_range(ws: dict, module: Any, from_seq: int, to_seq: int) -> list[str]:
    names = {
        str(c.get("id")): str(c.get("name") or "").strip()
        for c in (getattr(module, "clues", None) or []) if isinstance(c, dict) and c.get("id")
    }
    out: list[str] = []
    for cid, entry in (ws.get("clue_ledger") or {}).items():
        if not isinstance(entry, dict) or entry.get("status") not in ("known", "partial"):
            continue
        name = names.get(str(cid))
        if name and _in_range(entry.get("seq"), from_seq, to_seq) and name not in out:
            out.append(name)
    return out


def _npc_names(ws: dict, module: Any) -> dict[str, str]:
    defs = list(getattr(module, "npcs", None) or []) + world_memory.promoted_npc_cards(ws)
    return {
        str(n.get("id")): str(n.get("name") or "").strip()
        for n in defs if isinstance(n, dict) and n.get("id") and n.get("name")
    }


def _npcs_in_range(
    ws: dict, module: Any, events: list[Any], from_seq: int, to_seq: int,
) -> list[str]:
    names = _npc_names(ws, module)
    out: list[str] = []
    for nid, entry in (ws.get("npc_memory") or {}).items():
        if not isinstance(entry, dict) or nid not in names:
            continue
        if any(
            isinstance(it, dict) and _in_range(it.get("seq"), from_seq, to_seq)
            for it in entry.get("interactions") or []
        ) and names[nid] not in out:
            out.append(names[nid])
    # 开过口的 NPC（对白事件的说话人）：互动史是环形缓冲，可能已挤掉，这里再兜一层
    by_name = set(names.values())
    for ev in events or []:
        who = str(getattr(ev, "actor_name", "") or "").strip()
        if getattr(ev, "event_type", "") == "dialogue" and who in by_name and who not in out:
            out.append(who)
    return out


def _ngrams(text: str, n: int = 4) -> set[str]:
    s = _PUNCT.sub("", text or "")
    return {s[i:i + n] for i in range(len(s) - n + 1)} if len(s) >= n else ({s} if s else set())


def _promise_sentences(events: list[Any]) -> list[tuple[str, int, str]]:
    """本批原文里带承诺/约定标记的句子：(说话人, seq, 句子)。"""
    out: list[tuple[str, int, str]] = []
    for ev in events or []:
        if getattr(ev, "event_type", "") not in _TEXT_EVENT_TYPES:
            continue
        who = str(getattr(ev, "actor_name", "") or "").strip()
        for sent in _SENTENCE_SPLIT.split(getattr(ev, "content", "") or ""):
            sent = sent.strip()
            if 6 <= len(sent) and _PROMISE_RE.search(sent):
                out.append((who, int(getattr(ev, "sequence_num", 0) or 0), sent))
    return out


def _covered(sentence: str, corpus: str) -> bool:
    """句子的实质内容（去掉标记词）是否在章节 / 台账里有 4 字以上的重合片段。"""
    core = _PROMISE_RE.sub("", sentence)
    grams = _ngrams(core)
    corpus = _PUNCT.sub("", corpus or "")
    return bool(grams) and any(g in corpus for g in grams)


def _new_hooks_unmentioned(ws: dict, module: Any, flags: set[str], text: str) -> list[str]:
    prev = set((ws.get(AUDIT_KEY) or {}).get("flags") or [])
    out: list[str] = []
    for hook in plot_progress._hooks(module):
        if not plot_progress._hook_done(hook, flags) or not hook["set_flags"]:
            continue
        if (set(hook["set_flags"]) & flags) <= prev:
            continue  # 上次自检时就已触发
        title = hook["title"] or hook["id"]
        if title and not mentioned(title, text):
            out.append(title)
    return out


def _memo(clues: list[str], npcs: list[str]) -> str:
    parts = []
    if clues:
        parts.append("本段已发现线索" + "、".join(f"「{c}」" for c in clues))
    if npcs:
        parts.append("接触过的人物" + "、".join(f"「{n}」" for n in npcs))
    return MEMO_PREFIX + "；".join(parts) + "）" if parts else ""


def _patch_chapter(ws: dict, index: int, memo: str) -> dict:
    chapters = list(world_memory.story_chapters(ws))
    if not memo or not chapters:
        return ws
    ch = dict(chapters[index])
    ch["text"] = ch["text"].rstrip() + "\n" + memo
    chapters[index] = ch
    new_ws = dict(ws)
    new_ws[world_memory.STORY_CHAPTERS_KEY] = chapters
    new_ws["story_summary"] = world_memory.story_summary_text(new_ws)
    return new_ws


def audit_chapter(
    ws: dict, module: Any, flags: set[str], chapter_index: int = -1,
    events: list[Any] | None = None,
) -> tuple[dict, dict]:
    """对某一章做压缩后自检并确定性补救，返回 (新 ws, 报告)。纯函数，不碰数据库。

    ``events`` 是这一章刚浓缩掉的原文事件（滚动摘要时有；章节合并时为 None——那时原文
    不在手边，承诺扫描跳过，只对线索 / NPC / 进度对账）。
    """
    chapters = world_memory.story_chapters(ws)
    if not chapters or module is None:
        return ws, {}
    ch = chapters[chapter_index]
    text = ch.get("text") or ""
    lo, hi = int(ch.get("from_seq") or 0), int(ch.get("to_seq") or 0)

    missing_clues = [c for c in _clues_in_range(ws, module, lo, hi) if not mentioned(c, text)]
    missing_npcs = [
        n for n in _npcs_in_range(ws, module, events or [], lo, hi) if not mentioned(n, text)
    ]
    ws = _patch_chapter(ws, chapter_index, _memo(missing_clues, missing_npcs))

    added_threads: list[str] = []
    if events:
        corpus = text + "\n" + "\n".join(
            t.get("text", "") for t in plot_threads.threads(ws).values()
        )
        for who, seq, sent in _promise_sentences(events):
            if len(added_threads) >= MAX_PROMISES_PER_BATCH:
                break
            if _covered(sent, corpus):
                continue
            line = f"{who}：{sent}" if who else sent
            ws, tid = plot_threads.add_thread(ws, "promise", line, seq, source="audit")
            if tid:
                added_threads.append(line)
                corpus += "\n" + line

    hooks = _new_hooks_unmentioned(ws, module, flags, text)
    report = {
        "chapter_from_seq": lo, "chapter_to_seq": hi,
        "missing_clues": missing_clues, "missing_npcs": missing_npcs,
        "added_threads": added_threads, "hooks_unmentioned": hooks,
        "flags": sorted(flags),
    }
    ws = dict(ws)
    ws[AUDIT_KEY] = report
    if missing_clues or missing_npcs or added_threads or hooks:
        logger.info(
            "压缩自检：seq %s-%s 补记线索=%s NPC=%s 补台账承诺=%d 未提及的剧情进度=%s",
            lo, hi, missing_clues, missing_npcs, len(added_threads), hooks,
        )
    return ws, report
