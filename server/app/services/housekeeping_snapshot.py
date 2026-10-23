"""后台推演跨越 LLM 等待时的乐观校验，避免把旧快照写回新局面。

等待前记下 world_state、当前场景、事件尾序号及其之前全部事件的摘要；模型返回后重新读取比对。
``allow_append=True`` 时只允许「尾部追加新事件」：摘要/推演针对的是旧区间，新事件留给下一轮，
但旧区间里的事件被删改（重新生成回滚等）、world_state 或场景变化都视为过期。
"""
from copy import deepcopy
from dataclasses import dataclass
import hashlib

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.event_log import EventLog
from app.models.session import GameSession


def _tail(db: Session, session_id: str) -> int:
    return db.query(func.max(EventLog.sequence_num)).filter(EventLog.session_id == session_id).scalar() or 0


def _digest(db: Session, session_id: str, upto: int) -> str:
    h = hashlib.sha256()
    rows = db.query(EventLog.sequence_num, EventLog.event_type, EventLog.content).filter(
        EventLog.session_id == session_id, EventLog.sequence_num <= upto,
    ).order_by(EventLog.sequence_num)
    for seq, kind, content in rows:
        h.update(f"{seq}\x1f{kind}\x1f{content or ''}\x1e".encode())
    return h.hexdigest()


@dataclass(frozen=True)
class Snapshot:
    world_state: dict
    scene_id: str | None
    tail: int
    digest: str = ""

    @classmethod
    def capture(cls, db: Session, session: GameSession) -> "Snapshot":
        tail = _tail(db, session.id)
        return cls(
            deepcopy(session.world_state or {}), session.current_scene_id, tail,
            _digest(db, session.id, tail),
        )

    def is_current(self, db: Session, session: GameSession, *, allow_append: bool = False) -> bool:
        db.refresh(session)
        if self.world_state != (session.world_state or {}) or self.scene_id != session.current_scene_id:
            return False
        tail = _tail(db, session.id)
        if tail < self.tail or (tail != self.tail and not allow_append):
            return False
        return _digest(db, session.id, self.tail) == self.digest
