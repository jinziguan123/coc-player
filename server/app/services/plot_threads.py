"""剧情线台账：已做的决定、许下的承诺、未了结的悬念与待办（world_state.plot_threads）。

为什么单独建台账：这些东西以前只写进滚动摘要的散文。摘要一压（尤其二次合并），「答应了
酒保天亮前带回怀表」这种一句话的约束最容易被概括掉——而 KP 一旦忘了，玩家是直接可感的。
台账把它们外置成结构化条目：滚动摘要那次调用顺带产出差量（新增 / 了结），这里校验后落库，
KP 上下文的易变段每轮注入全部未了结项。

形态（与 clue_ledger 一样是 world_state 里的 dict，按 id 建键）::

    {"t3": {"kind": "promise", "text": "答应酒保天亮前带回怀表",
            "status": "open", "since_seq": 120,
            "resolved_seq": 0, "resolution": ""}}

**玩家可见性**：整份台账是守秘人资料——可能记着「KP 知道而玩家不知道」的悬念（谁在撒谎、
哪条线还埋着）。world_state 下发走白名单（api/sessions._PLAYER_WORLD_STATE_KEYS），本键不在
其中；它只注入 KP 上下文，不进队友 / NPC 的上下文，也不广播。
"""

from __future__ import annotations

import re

KINDS: dict[str, str] = {
    "decision": "决定",
    "promise": "承诺",
    "mystery": "悬念",
    "todo": "待办",
}
_KIND_ALIASES = {
    "决定": "decision", "承诺": "promise", "悬念": "mystery", "待办": "todo",
    "thread": "mystery", "question": "mystery", "task": "todo", "goal": "todo",
}
TEXT_MAX_CHARS = 160
RESOLUTION_MAX_CHARS = 80
#: 单次差量最多新增几条：防抽取器把整段剧情拆成几十条「待办」灌爆台账
MAX_NEW_PER_DELTA = 8
#: 已了结的只留最近这些（供自检/回溯参考）；未了结的一条不丢——那正是要守住的
MAX_RESOLVED_KEPT = 20


def _norm_kind(raw) -> str:
    k = str(raw or "").strip().lower()
    k = _KIND_ALIASES.get(k, k)
    return k if k in KINDS else "todo"


def _clean(text, limit: int) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    return s if len(s) <= limit else s[:limit - 1] + "…"


def threads(ws: dict | None) -> dict[str, dict]:
    raw = (ws or {}).get("plot_threads")
    return {k: v for k, v in raw.items() if isinstance(v, dict)} if isinstance(raw, dict) else {}


def open_threads(ws: dict | None) -> list[tuple[str, dict]]:
    """未了结项，按出现先后（since_seq）排序。"""
    items = [(tid, t) for tid, t in threads(ws).items() if t.get("status") != "resolved"]
    return sorted(items, key=lambda kv: (int(kv[1].get("since_seq") or 0), kv[0]))


def _next_id(existing: dict) -> str:
    n = 0
    for tid in existing:
        m = re.fullmatch(r"t(\d+)", str(tid))
        if m:
            n = max(n, int(m.group(1)))
    return f"t{n + 1}"


def add_thread(
    ws: dict, kind: str, text: str, seq: int, source: str = "summary",
) -> tuple[dict, str | None]:
    """新增一条（同文去重：已有同文未了结项则不重复记）。返回 (新 ws, 新 id 或 None)。

    ``source``：summary = 滚动摘要抽取；audit = 压缩后自检从原文补回。
    """
    text = _clean(text, TEXT_MAX_CHARS)
    if not text:
        return ws, None
    ws = dict(ws or {})
    existing = dict(threads(ws))
    if any(t.get("text") == text and t.get("status") != "resolved" for t in existing.values()):
        return ws, None
    tid = _next_id(existing)
    existing[tid] = {
        "kind": _norm_kind(kind), "text": text, "status": "open",
        "since_seq": int(seq or 0), "resolved_seq": 0, "resolution": "",
        "source": source,
    }
    ws["plot_threads"] = existing
    return ws, tid


def _prune_resolved(items: dict) -> dict:
    resolved = sorted(
        (kv for kv in items.items() if kv[1].get("status") == "resolved"),
        key=lambda kv: int(kv[1].get("resolved_seq") or 0),
    )
    for tid, _ in resolved[:-MAX_RESOLVED_KEPT] if len(resolved) > MAX_RESOLVED_KEPT else []:
        items.pop(tid, None)
    return items


def apply_thread_delta(ws: dict, delta, from_seq: int, to_seq: int) -> dict:
    """合并滚动摘要抽取出的剧情线差量（未经校验的模型输出，这里做全部安全处理）。

    ``delta`` 形如 ``{"new": [{"kind", "text"}], "resolved": [{"id", "how"}]}``：
    - new：kind 归一到四类，text 截断去重，单次最多 ``MAX_NEW_PER_DELTA`` 条；
    - resolved：只认台账里**已存在且未了结**的 id（编出来的一律忽略）。
    非 dict / 字段形状不对一律当空处理，绝不抛异常。
    """
    if not isinstance(delta, dict):
        return ws
    ws = dict(ws or {})
    for item in (delta.get("resolved") or [])[:50] if isinstance(delta.get("resolved"), list) else []:
        tid, how = (item, "") if isinstance(item, str) else (
            (item.get("id"), item.get("how") or item.get("resolution") or "")
            if isinstance(item, dict) else (None, "")
        )
        items = dict(threads(ws))
        entry = items.get(str(tid or "").strip())
        if not entry or entry.get("status") == "resolved":
            continue
        items[str(tid).strip()] = {
            **entry, "status": "resolved", "resolved_seq": int(to_seq or 0),
            "resolution": _clean(how, RESOLUTION_MAX_CHARS),
        }
        ws["plot_threads"] = items
    new = delta.get("new") if isinstance(delta.get("new"), list) else []
    for item in new[:MAX_NEW_PER_DELTA]:
        if isinstance(item, str):
            kind, text = "todo", item
        elif isinstance(item, dict):
            kind, text = item.get("kind"), item.get("text") or item.get("content")
        else:
            continue
        ws, _ = add_thread(ws, kind, text, from_seq)
    if "plot_threads" in ws:
        ws["plot_threads"] = _prune_resolved(dict(ws["plot_threads"]))
    return ws


def format_threads_brief(ws: dict | None) -> str:
    """给摘要抽取器的输入：未了结项 ``- id [类型] 原话``（它据此判断哪些在新事件里了结了）。"""
    return "\n".join(
        f"- {tid} [{KINDS.get(t.get('kind'), '待办')}] {t.get('text', '')}"
        for tid, t in open_threads(ws)
    )


def format_threads_section(ws: dict | None) -> str:
    """KP 上下文小节：全部未了结项（KP 专属）。没有则空串（不渲染小节）。"""
    items = open_threads(ws)
    if not items:
        return ""
    lines = [
        "【未了结的剧情线】（KP 专属台账——早先剧情里定下、至今仍有效的决定/承诺/悬念/待办；"
        "原话即约束，叙事不得与之矛盾，择机推进或回收，不要当作已经了结）"
    ]
    for _tid, t in items:
        lines.append(f"- [{KINDS.get(t.get('kind'), '待办')}] {t.get('text', '')}")
    return "\n".join(lines)
