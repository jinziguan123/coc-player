"""本局运行统计独立表：拆自 world_state.session_usage / turn_usage / rag_stats。

这些是 append-only 的「运行统计」（token 用量、RAG 检索命中质量），不该与剧情记忆
（flags/clue_ledger/npc_memory…）挤在同一 JSON 列里整段回写（ADR-003 决策第 4 条）。

1:1 挂在 game_sessions：session_id 即主键（不复用 UUIDMixin）。删会话时经
GameSession.session_stats 关系（cascade="all, delete-orphan"）一并清理。

预算校准系数 budget_scale 刻意**不**迁到这里：它被纯函数 build_kp_context 每轮读取，
搬走会让上下文构建引入 DB 访问，得不偿失。它留在 world_state（见 _record_turn_usage）。
"""

from __future__ import annotations

from sqlalchemy import JSON, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class SessionStats(Base, TimestampMixin):
    __tablename__ = "session_stats"

    session_id: Mapped[str] = mapped_column(
        ForeignKey("game_sessions.id"), primary_key=True
    )
    # 本局累计 token 用量（单调累增）：planner/主叙事/validator/队友/子代理/战斗… 的合计。
    session_usage: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    # 上一回合主叙事那次调用的服务端真实 usage（供「上下文占用」显示实测值）。
    turn_usage: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    # RAG（规则书/模组原文检索）调用与命中质量统计。
    rag_stats: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    version: Mapped[int] = mapped_column(default=1, server_default="1")
