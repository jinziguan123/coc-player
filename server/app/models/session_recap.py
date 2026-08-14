"""战报独立表：拆自 world_state.recaps 的 1:N 结构化表。

每条战报一行：session_id 外键 + ordinal（追加顺序）+ entry（单条战报 dict）。
world_state.recaps 是一个 append-only 列表，拆成 1:N 表后可按会话查询、按 ordinal 稳定排序，
不再与剧情记忆挤在同一 JSON 列里整段回写（ADR-003）。

删除会话时经 GameSession.recaps 关系（cascade="all, delete-orphan"）一并清理。
"""

from __future__ import annotations

from sqlalchemy import JSON, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class SessionRecap(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "session_recaps"

    session_id: Mapped[str] = mapped_column(
        ForeignKey("game_sessions.id"), index=True
    )
    # 追加顺序（list_recaps 按此排序，稳定；不做 max+1 并发保护——战报按请求串行生成）。
    ordinal: Mapped[int] = mapped_column(default=0)
    entry: Mapped[dict] = mapped_column(JSON, default=dict)
