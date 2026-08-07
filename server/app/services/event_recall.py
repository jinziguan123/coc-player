"""本局事件原文的向量索引与回捞。

**为什么需要它。** 滚动摘要是纯前向的有损压缩：老事件被浓缩进 story_summary，游标只前进
不后退，每次浓缩又是在上一次的产物上做的（误差复利）。事件原文一直躺在库里，但上下文里
没有任何通路能把它取回来——RAG 只覆盖规则书与模组原文，不覆盖「本局已经发生过的事」。
结果是长局必然失忆：玩家提起三章前当铺老板说的暗号，KP 在结构上不可能想起来。

这个模块补上那条通路，把记忆结构从「有损管道」变成「有损缓存 + 无损底座」。

**选型。** 复用规则书 RAG 已经跑通的那一套：fastembed + bge-small-zh + SQLite BLOB +
numpy 暴力余弦（``vector_search.cosine_top_k``）。事件规模与规则书同量级（千级），不引入
向量库，也不引入 MemGPT / mem0 这类记忆框架——它们绑定整套 agent 运行时，与本项目的
Provider 抽象 + RuleEngine 架构冲突。

**全程 fail-open**：嵌入器加载失败、维度不匹配、库里没索引……一律退回「查不到」，
绝不阻塞跑团。
"""

from __future__ import annotations

import logging

import numpy as np
from sqlalchemy.orm import Session

from app.ai.embedding import Embedder, get_embedder
from app.models.event_log import EventLog
from app.services.vector_search import cosine_top_k

logger = logging.getLogger(__name__)

#: 只索引这几类事件。dice/system 是机械噪音（「侦查 检定成功」），检索价值低且会稀释
#: 语义空间；ooc 是场外发言，本就不属于故事。
INDEXABLE_TYPES = ("narration", "dialogue", "action")
#: 短于此长度的事件不索引：「我搜查」这种既检索不到也不值得占一行向量。
MIN_INDEX_CHARS = 12
#: 单次 housekeeping 最多嵌入多少条，避免存量长局第一次补索引时卡住收尾流程。
MAX_INDEX_BATCH = 200
#: 命中一条事件时，连同前后各几条一起返回。单条台词脱离上下文没有意义——
#: 与规则书 RAG「相邻块合并」同一个道理。
CONTEXT_WINDOW = 2
#: 当前场景的事件加权：同一地点发生过的事，多半才是玩家此刻问的那件。
SCENE_BOOST = 1.3


def is_enabled(game_session) -> bool:
    """是否向 KP 广告「回想往事」能力。

    条件是**本局确有剧情被滚动摘要浓缩掉**（游标非 0）。游标为 0 时全部历史仍逐字躺在
    上下文里，广告了只会诱导 KP 去查它眼前就有的东西，白花一次查阅配额。
    """
    return bool((getattr(game_session, "world_state", None) or {}).get("story_summary_seq"))


def _indexable(ev: EventLog) -> bool:
    return (
        (ev.event_type or "") in INDEXABLE_TYPES
        and len((ev.content or "").strip()) >= MIN_INDEX_CHARS
    )


def index_pending(db: Session, session_id: str, embedder: Embedder | None = None) -> int:
    """给该会话尚未索引的事件补上向量，返回本次索引条数。

    在 housekeeping 窗口调用（与滚动摘要同一时机）：那时本就在做后台收尾，且正好是
    「这批事件要被浓缩了」的时刻——它们即将失去逐字原文，索引它们的动机最强。
    """
    try:
        rows = (
            db.query(EventLog)
            .filter(EventLog.session_id == session_id, EventLog.embedding.is_(None))
            .order_by(EventLog.sequence_num)
            .all()
        )
        todo = [e for e in rows if _indexable(e)][:MAX_INDEX_BATCH]
        if not todo:
            return 0
        embedder = embedder or get_embedder()
        vectors = embedder.embed_passages([(e.content or "").strip() for e in todo])
        for ev, vec in zip(todo, vectors):
            ev.embedding = np.asarray(vec, dtype=np.float32).tobytes()
        db.commit()
        logger.info("事件向量索引：session=%s 新增 %s 条", session_id, len(todo))
        return len(todo)
    except Exception:
        logger.exception("事件向量索引失败（忽略）: session=%s", session_id)
        db.rollback()
        return 0


def _window(events: list[EventLog], center: int) -> list[EventLog]:
    lo = max(0, center - CONTEXT_WINDOW)
    hi = min(len(events), center + CONTEXT_WINDOW + 1)
    return events[lo:hi]


def recall(
    db: Session,
    session_id: str,
    query: str,
    k: int = 3,
    scene_id: str | None = None,
    before_seq: int | None = None,
    embedder: Embedder | None = None,
) -> list[dict]:
    """按语义检索本局历史，返回 ``[{"seq", "events": [EventLog, ...], "score"}, ...]``。

    ``before_seq`` 给定时只在该 seq 之前的事件里找——回捞的用途是「想起被浓缩掉的往事」，
    近段事件本来就在上下文里逐字躺着，再查一遍纯属浪费。
    """
    query = (query or "").strip()
    if not query:
        return []
    try:
        q = db.query(EventLog).filter(
            EventLog.session_id == session_id,
            EventLog.embedding.is_not(None),
        )
        if before_seq is not None:
            q = q.filter(EventLog.sequence_num < before_seq)
        rows = q.order_by(EventLog.sequence_num).all()
        if not rows:
            return []

        embedder = embedder or get_embedder()
        weights = None
        if scene_id:
            weights = [
                SCENE_BOOST if (r.metadata_ or {}).get("scene_id") == scene_id else 1.0
                for r in rows
            ]
        top = cosine_top_k(
            [r.embedding for r in rows], embedder.embed_query(query), k, weights=weights,
        )

        # 窗口在**完整事件流**上展开，而不是在已索引的子集上。短台词与骰点结果不进索引
        # （见 INDEXABLE_TYPES / MIN_INDEX_CHARS），但它们常常正是命中点前后最关键的一句
        # ——暗号原话、检定成败。只在索引子集上开窗，恰好会把这些漏掉。
        tl_q = db.query(EventLog).filter(
            EventLog.session_id == session_id,
            EventLog.event_type != "ooc",   # 场外发言不属于故事
        )
        if before_seq is not None:
            tl_q = tl_q.filter(EventLog.sequence_num < before_seq)
        timeline = tl_q.order_by(EventLog.sequence_num).all()
        pos = {e.sequence_num: i for i, e in enumerate(timeline)}

        # 命中点可能彼此相邻，窗口展开后会重叠；按 seq 去重，避免同一段原文重复注入。
        out: list[dict] = []
        used: set[int] = set()
        for idx, score in top:
            center = pos.get(rows[idx].sequence_num)
            if center is None:
                continue
            win = [e for e in _window(timeline, center) if e.sequence_num not in used]
            if not win:
                continue
            used.update(e.sequence_num for e in win)
            out.append({"seq": rows[idx].sequence_num, "events": win, "score": score})
        return out
    except Exception:
        logger.exception("事件回捞失败（忽略）: session=%s", session_id)
        return []


def format_recall(hits: list[dict]) -> str:
    """把回捞结果渲染成给 KP 看的原文片段（保留说话人与顺序）。"""
    if not hits:
        return "（没有找到相关的往事记录。）"
    blocks: list[str] = []
    for h in hits:
        lines = []
        for ev in h["events"]:
            content = (ev.content or "").replace("\n", " ").strip()
            if not content:
                continue
            etype = ev.event_type or ""
            if etype == "narration":
                lines.append(f"旁白：{content}")
            elif etype == "dialogue":
                lines.append(f"{ev.actor_name or 'NPC'}：{content}")
            else:
                lines.append(f"{ev.actor_name or '某人'}（行动）：{content}")
        if lines:
            blocks.append("\n".join(lines))
    return "\n\n———\n\n".join(blocks) if blocks else "（没有找到相关的往事记录。）"
