"""本局运行统计（session_usage / turn_usage / rag_stats）的唯一读写口径。

拆自 world_state 的三个键，落到 1:1 的 session_stats 表。读写都走这里：
- 读：get(db, session_id) → SessionStats | None
- 写：get_or_create(db, session_id) → SessionStats（未提交前由调用方 db.commit）
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.session_stats import SessionStats


def get(db: Session, session_id: str) -> SessionStats | None:
    return db.get(SessionStats, session_id)


def get_or_create(db: Session, session_id: str) -> SessionStats:
    row = db.get(SessionStats, session_id)
    if row is None:
        row = SessionStats(session_id=session_id)
        db.add(row)
    return row
