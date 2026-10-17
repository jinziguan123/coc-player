"""Agent 调用轨迹表 agent_trace_logs

Revision ID: b4e6f8a0c2d3
Revises: a3d5f7b9c1e2
Create Date: 2026-10-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b4e6f8a0c2d3"
down_revision: Union[str, Sequence[str], None] = "a3d5f7b9c1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "agent_trace_logs" in inspector.get_table_names():
        return
    op.create_table(
        "agent_trace_logs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "session_id", sa.String(),
            sa.ForeignKey("game_sessions.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("agent", sa.String(32), nullable=False),
        sa.Column("stage", sa.String(64), nullable=False, server_default=""),
        sa.Column("outcome", sa.String(32), nullable=False, server_default="ok"),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_agent_trace_logs_session_id", "agent_trace_logs", ["session_id"],
    )
    op.create_index("ix_agent_trace_logs_agent", "agent_trace_logs", ["agent"])


def downgrade() -> None:
    op.drop_index("ix_agent_trace_logs_agent", table_name="agent_trace_logs")
    op.drop_index("ix_agent_trace_logs_session_id", table_name="agent_trace_logs")
    op.drop_table("agent_trace_logs")
