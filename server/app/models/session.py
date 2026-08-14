from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import JSON, Enum, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.chase_state import ChaseState
    from app.models.combat_state import CombatState
    from app.models.session_participant import SessionParticipant
    from app.models.session_recap import SessionRecap
    from app.models.session_stats import SessionStats


class GameSession(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "game_sessions"

    module_id: Mapped[str] = mapped_column(ForeignKey("modules.id"))
    status: Mapped[str] = mapped_column(
        Enum("setup", "active", "paused", "ended", name="session_status"),
        default="setup",
    )
    # KP 来源：ai 为兼容旧局的默认模式，human 表示由真人 KP 席位驱动。
    kp_mode: Mapped[str] = mapped_column(
        Enum("ai", "human", name="kp_mode"), default="ai", server_default="ai",
    )
    # 房主身份独立于玩家角色席。旧房间该字段为空，由服务层回落到主角席兼容判定。
    host_token: Mapped[str | None] = mapped_column(nullable=True, index=True)
    # 1=旧席位语义，2=KP/玩家严格分离且 token 单席位。
    identity_version: Mapped[int] = mapped_column(default=1, server_default="1")
    # 房间分享码（阶段 2 联机：他人凭码加入认领空席）；建房时生成、唯一
    room_code: Mapped[str | None] = mapped_column(nullable=True, index=True)
    # 主角快捷字段：与 session_participants 中 is_primary 的席位对齐，便于兼容旧代码与展示。
    player_character_id: Mapped[str | None] = mapped_column(
        ForeignKey("characters.id"), nullable=True
    )
    current_scene_id: Mapped[str | None] = mapped_column(nullable=True)
    world_state: Mapped[dict] = mapped_column(JSON, default=dict)
    # 真人 KP 私有工作区：笔记、自动队友偏好等。绝不加入 SessionRead，避免玩家端读取。
    kp_state: Mapped[dict] = mapped_column(
        JSON, default=dict, server_default="{}",
    )
    turn_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # 本局的文风 / 画风（预设 id 或自定义原文，取值约定见 services.style_presets）。
    # ""=继承模组的默认值；模组也没设时，文风不注入、画风回落到默认那一档。
    narrative_style: Mapped[str] = mapped_column(default="", server_default="")
    image_style: Mapped[str] = mapped_column(default="", server_default="")

    participants: Mapped[list["SessionParticipant"]] = relationship(
        "SessionParticipant",
        cascade="all, delete-orphan",
        order_by="SessionParticipant.seat_order",
    )
    # 战斗态 1:1（拆自 world_state.combat）：删会话时经 cascade 一并清理。
    combat_state: Mapped["CombatState | None"] = relationship(
        "CombatState", cascade="all, delete-orphan", uselist=False,
    )
    # 追逐态 1:1（拆自 world_state.chase）：同上。
    chase_state: Mapped["ChaseState | None"] = relationship(
        "ChaseState", cascade="all, delete-orphan", uselist=False,
    )
    # 运行统计 1:1（拆自 world_state.session_usage/turn_usage/rag_stats）：同上。
    session_stats: Mapped["SessionStats | None"] = relationship(
        "SessionStats", cascade="all, delete-orphan", uselist=False,
    )
    # 战报 1:N（拆自 world_state.recaps）：删会话时经 cascade 一并清理。
    recaps: Mapped[list["SessionRecap"]] = relationship(
        "SessionRecap", cascade="all, delete-orphan", order_by="SessionRecap.ordinal",
    )
