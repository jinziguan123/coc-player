"""导航态独立表：拆自 world_state.party_locations / visited_scenes。

队伍的「位置/导航」状态（各角色所在场景 + 真正到访过的场景）单独成表，不再与剧情记忆
（flags/clue_ledger/npc_memory…）挤在同一 JSON 列里整段回写（ADR-003）。

1:1 挂在 game_sessions：session_id 即主键（不复用 UUIDMixin）。删会话时经
GameSession.navigation 关系（cascade="all, delete-orphan"）一并清理。
"""

from __future__ import annotations

from sqlalchemy import JSON, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class SessionNavigation(Base, TimestampMixin):
    __tablename__ = "session_navigation"

    session_id: Mapped[str] = mapped_column(
        ForeignKey("game_sessions.id"), primary_key=True
    )
    # {角色 id: 所在场景 id}；缺省时调用方回落到 current_scene_id（向后兼容）。
    party_locations: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    # 队伍真正到访过的场景 id（只进不出，含初始场景）。
    visited_scenes: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    version: Mapped[int] = mapped_column(default=1, server_default="1")
