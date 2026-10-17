"""NPC / 战斗动作意图历史（append-only）。

从 world_state.intent_history 迁出的独立表：动作历史会持续增长，JSON 列每次整段
回写既浪费又难筛选。与 agent_trace_logs 一样，只服务 KP 诊断，不进入玩家视图。
"""
from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


def _uuid() -> str:
    return str(uuid.uuid4())


class IntentHistory(Base, TimestampMixin):
    __tablename__ = "intent_history"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("game_sessions.id", ondelete="CASCADE"), index=True,
    )
    actor_id: Mapped[str] = mapped_column(String(64), default="")
    actor_name: Mapped[str] = mapped_column(String(128), default="")
    source: Mapped[str] = mapped_column(String(16), index=True, default="")
    action: Mapped[str] = mapped_column(String(32), default="")
    target: Mapped[str] = mapped_column(String(256), default="")
    verb: Mapped[str] = mapped_column(String(32), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    observes_seq: Mapped[int] = mapped_column(Integer, default=0)
    target_version: Mapped[int] = mapped_column(Integer, default=0)
    precondition_ok: Mapped[bool] = mapped_column(default=True)
