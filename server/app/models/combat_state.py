"""战斗态独立表：把 world_state.combat 的活跃战斗状态机拆到结构化表。

1:1 挂在 game_sessions：session_id 即主键（不复用 UUIDMixin）。删除会话时经
GameSession.combat_state 关系（cascade="all, delete-orphan"）一并清理。

state 仍是 JSON：战斗态是深层嵌套 dict（先攻队列 / 方格棋盘 / pending_roll /
pending_reaction / log），逐字段拆列既不现实也无收益。拆表的意义在于——
  ① 战斗态与剧情记忆（world_state）彻底分家，各自生命周期 / 版本 / 备份独立；
  ② 高频读写的战斗态不再与低频剧情字段挤在同一列里整段回写。

version 独立维护，为将来战斗态的字段迁移留 hook（对齐 ADR-003 决策第 5 条）。
"""

from __future__ import annotations

from sqlalchemy import JSON, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class CombatState(Base, TimestampMixin):
    __tablename__ = "combat_states"

    session_id: Mapped[str] = mapped_column(
        ForeignKey("game_sessions.id"), primary_key=True
    )
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(default=1, server_default="1")
