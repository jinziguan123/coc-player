"""席位与队员的只读查询；不依赖会话编排或规则配置。"""

from sqlalchemy.orm import Session

from app.models.character import Character
from app.models.session_participant import SessionParticipant


def get_participants(db: Session, session_id: str) -> list[SessionParticipant]:
    return (
        db.query(SessionParticipant)
        .filter(SessionParticipant.session_id == session_id)
        .order_by(SessionParticipant.seat_order.asc())
        .all()
    )


def get_party_members(
    db: Session, session_id: str, exclude_id: str | None = None,
) -> list[Character]:
    """会话内所有已填角色（真人 + AI），可排除某角色；用于 KP 整队上下文。"""
    out: list[Character] = []
    for p in get_participants(db, session_id):
        if not p.character_id or p.character_id == exclude_id:
            continue
        c = db.get(Character, p.character_id)
        if c:
            out.append(c)
    return out

