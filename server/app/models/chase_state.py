"""追逐态独立表：把 world_state.chase 的活跃追逐状态机拆到结构化表。

1:1 挂在 game_sessions：session_id 即主键（不复用 UUIDMixin）。删除会话时经
GameSession.chase_state 关系（cascade="all, delete-orphan"）一并清理。

与 combat_states 同一理由：追逐态是高频强一致的状态机（gap 距离轨 / 双方 mov / 判定
阈值），不该与低频剧情字段挤在同一列里整段回写。state 仍是 JSON（深层嵌套 dict）。
"""

from __future__ import annotations

from sqlalchemy import JSON, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ChaseState(Base, TimestampMixin):
    __tablename__ = "chase_states"

    session_id: Mapped[str] = mapped_column(
        ForeignKey("game_sessions.id"), primary_key=True
    )
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(default=1, server_default="1")
