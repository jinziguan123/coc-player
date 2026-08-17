"""会话事件仓库：事件分页、全文检索、暂存发言与事件落库。

自 ``session_service.py`` 拆出（拆表同期治理 P1：session_service 边界过宽）。
事件查询/写入是独立职责簇：只依赖 EventLog/GameSession 与序列号唯一约束，
不参与席位、授权与导航。``session_service`` 保留同名 re-export，旧导入路径继续可用。
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.models.event_log import EventLog
from app.models.session import GameSession

KP_ONLY_SENTINEL = "kp"


def is_kp_only_event(ev: EventLog) -> bool:
    """该事件是否「仅 KP 可见」（visibility 含 kp 哨兵）——玩家侧查询一律过滤。"""
    return KP_ONLY_SENTINEL in (ev.visibility or [])


def get_session_events(
    db: Session, session_id: str, limit: int = 0, offset: int = 0
) -> list[EventLog]:
    """按 sequence_num 升序返回会话事件；默认 limit=0 即全量。

    默认必须是「全量」而非截断：本函数只服务于生成/上下文构建路径，它们要的是完整对话史
    （由 build_kp_context 的 token 预算 + 滚动摘要游标负责裁剪成实际喂给 LLM 的窗口）。
    早先默认 limit=100 会因升序取到「最早的 100 条」——会话过百条后 KP 上下文里全是旧事件、
    看不到最新玩家输入，导致跑团错乱。前端历史/重连分页走的是另一个 get_latest_events
    （带 before_seq），不受此默认影响。
    """
    q = (
        db.query(EventLog)
        .filter(EventLog.session_id == session_id)
        .order_by(EventLog.sequence_num.asc())
        .offset(offset)
    )
    if limit > 0:
        q = q.limit(limit)
    return q.all()


def get_latest_events(
    db: Session, session_id: str, limit: int = 50, before_seq: int | None = None,
) -> tuple[list[EventLog], bool]:
    """前端历史/重连分页用的最新事件页（升序返回）。

    「仅 KP 可见」事件（visibility 含 kp 哨兵，如幕后推演）在此过滤——本端点面向
    所有玩家，幕后事件永远不下发前端。过滤在取页之后做（幕后事件稀疏），某页可能
    略少于 limit，但 has_more/before_seq 分页语义不受影响。
    """
    q = db.query(EventLog).filter(EventLog.session_id == session_id)
    if before_seq is not None:
        q = q.filter(EventLog.sequence_num < before_seq)
    q = q.order_by(EventLog.sequence_num.desc())
    rows = q.limit(limit + 1).all()
    has_more = len(rows) > limit
    results = [e for e in rows[:limit] if not is_kp_only_event(e)]
    results.reverse()
    return results, has_more


#: 参与检索的事件类型（系统提示、幕后推演等噪音不进）。
SEARCHABLE_EVENT_TYPES = ("narration", "dialogue", "action", "dice", "ooc")

#: 检索结果片段的长度上限，以及关键词左侧留多少上文。
SNIPPET_CHARS = 160
SNIPPET_LEAD = 40


def search_snippet(content: str, query: str, width: int = SNIPPET_CHARS) -> str:
    """截一段**以命中处为中心**的片段，两端截断处补省略号。

    原先是无脑取正文前 140 字。一段旁白动辄两三百字，关键词落在后半截时切下来的
    片段里根本看不到它——玩家看到的就是「这条明明不含关键词，怎么也被搜出来了」。
    匹配是在全文上做的，展示也得对得上。
    """
    text = (content or "").strip()
    q = (query or "").strip()
    if len(text) <= width:
        return text
    idx = text.lower().find(q.lower()) if q else -1
    if idx < 0:                                  # 查不到（大小写/空白差异）→ 退回取开头
        return text[:width].rstrip() + "…"
    start = max(0, idx - SNIPPET_LEAD)
    end = min(len(text), start + width)
    start = max(0, min(start, end - width))      # 命中在结尾附近时把窗口往左推满
    return ("…" if start > 0 else "") + text[start:end].strip() + ("…" if end < len(text) else "")


def search_events(
    db: Session, session_id: str, query: str, limit: int = 20,
    offset: int = 0, order: str = "desc",
) -> tuple[list[EventLog], int]:
    """在本局历史里模糊检索（content LIKE），返回 (本页事件, 命中总数)。

    ``order``：``desc`` 由新到旧（默认，最近发生的先看），``asc`` 由旧到新。
    命中总数供前端画分页——没有它，用户不知道自己在多大的结果集里翻。
    """
    q = (query or "").strip()
    if not q:
        return [], 0
    like = f"%{q}%"
    base = db.query(EventLog).filter(
        EventLog.session_id == session_id,
        EventLog.content.like(like),
        EventLog.event_type.in_(SEARCHABLE_EVENT_TYPES),
    )
    total = base.count()
    col = EventLog.sequence_num
    rows = (
        base.order_by(col.asc() if order == "asc" else col.desc())
        .offset(max(0, offset))
        .limit(max(1, limit))
        .all()
    )
    # 双保险：幕后事件（event_type=system）本就被类型过滤挡住，这里再按 kp 哨兵
    # 显式过滤一次，防未来搜索范围扩大后泄露「仅 KP 可见」内容。
    return [e for e in rows if not is_kp_only_event(e)], total
def delete_pending_event(db: Session, session_id: str, event_id: str, actor_id: str) -> bool:
    """删除一条『本回合暂存』发言：仅限本人、仅限 pending_turn（未推进）。返回是否删除。"""
    ev = db.get(EventLog, event_id)
    if not ev or ev.session_id != session_id:
        return False
    if not ev.actor_id or ev.actor_id != actor_id:
        return False
    if not (ev.metadata_ or {}).get("pending_turn"):
        return False
    db.delete(ev)
    db.commit()
    return True


def update_pending_event(
    db: Session, session_id: str, event_id: str, actor_id: str, content: str,
) -> bool:
    """改写一条『本回合暂存』发言的正文：仅限本人、仅限 pending_turn（未推进）。返回是否改写。"""
    ev = db.get(EventLog, event_id)
    if not ev or ev.session_id != session_id:
        return False
    if not ev.actor_id or ev.actor_id != actor_id:
        return False
    if not (ev.metadata_ or {}).get("pending_turn"):
        return False
    ev.content = content
    db.add(ev)
    db.commit()
    return True


def get_next_sequence_num(db: Session, session_id: str) -> int:
    result = (
        db.query(EventLog.sequence_num)
        .filter(EventLog.session_id == session_id)
        .order_by(EventLog.sequence_num.desc())
        .first()
    )
    return (result[0] + 1) if result else 1


def add_event(
    db: Session,
    session_id: str,
    event_type: str,
    content: str,
    actor_id: str | None = None,
    actor_name: str = "",
    visibility: list[str] | None = None,
    metadata: dict | None = None,
    group: str | None = None,
) -> EventLog:
    meta = dict(metadata or {})
    # 分头行动：同一回合里不同分组/场景的内容，用 group 标签分栏渲染（KP 经 [GROUP] 标注）。
    if group:
        meta["group"] = group
    # 给事件打上「发生在哪个场景」的戳：NPC 上下文据此只看自己所在场景的事件，
    # 避免一个 NPC 知道玩家在别处发生的事（信息隔离）。调用方未显式给 scene_id 时取当前场景。
    if "scene_id" not in meta:
        sess = db.get(GameSession, session_id)
        if sess and sess.current_scene_id:
            meta["scene_id"] = sess.current_scene_id

    # sequence_num 由「读最大值 + 1」生成，多个请求并发时可能同时读到同一个值。
    # 唯一约束负责兜底，遇到撞号只回滚本次 INSERT 并重新取最大值；其它完整性错误原样抛出。
    for attempt in range(3):
        event = EventLog(
            session_id=session_id,
            sequence_num=get_next_sequence_num(db, session_id),
            event_type=event_type,
            actor_id=actor_id,
            actor_name=actor_name,
            content=content,
            visibility=visibility or [],
            metadata_=meta,
        )
        db.add(event)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            message = str(exc).lower()
            is_sequence_conflict = (
                "uq_event_logs_session_sequence" in message
                or "event_logs.session_id, event_logs.sequence_num" in message
            )
            if not is_sequence_conflict or attempt == 2:
                raise
            continue
        db.refresh(event)
        return event

    # 理论上第三次尝试会在 attempt == 2 时直接抛出；保留显式异常避免静态分析认为无返回。
    raise RuntimeError("事件序号分配失败")


def set_event_group(db: Session, event: EventLog, group: str) -> None:
    """给已落库的事件补打分组标签（分头行动：把本回合各角色行动归入其所在场景列）。"""
    meta = dict(event.metadata_ or {})
    if meta.get("group") == group:
        return
    meta["group"] = group
    event.metadata_ = meta
    flag_modified(event, "metadata_")  # JSON 列原地改字典不会被脏检测，需显式标记
    db.add(event)
    db.commit()
