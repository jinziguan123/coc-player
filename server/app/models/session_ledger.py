"""进度台账独立表：拆自 world_state.san_checked / scene_events_seen。

会话级的「幂等台账」——哪些角色对哪些恐怖源做过理智检定、哪些场景机制点已经触发过——
单独成表，不再与剧情记忆挤在同一 JSON 列里整段回写（ADR-003）。

1:1 挂在 game_sessions：session_id 即主键（不复用 UUIDMixin）。删会话时经
GameSession.ledger 关系（cascade="all, delete-orphan"）一并清理。
"""

from __future__ import annotations

from sqlalchemy import JSON, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class SessionLedger(Base, TimestampMixin):
    __tablename__ = "session_ledger"

    session_id: Mapped[str] = mapped_column(
        ForeignKey("game_sessions.id"), primary_key=True
    )
    # 已做过理智检定的「恐怖源|角色id」集合（同一角色对同一源只检一次）。
    san_checked: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    # 已触发过的场景机制点幂等键（"scene:{id}:{index}" 等 → True）。
    scene_events_seen: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    version: Mapped[int] = mapped_column(default=1, server_default="1")
