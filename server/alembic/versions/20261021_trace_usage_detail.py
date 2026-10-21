"""轨迹记全段用量明细 + 本局按环节的用量账本

agent_trace_logs 增加 reasoning_tokens / cache_read_tokens / cache_write_tokens / calls；
session_stats 增加 usage_by_agent（按环节累计，供面板与本局累计对账）。

Revision ID: d9f1b3c5e7a9
Revises: c8e0a2b4d6f7
Create Date: 2026-10-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d9f1b3c5e7a9"
down_revision: Union[str, Sequence[str], None] = "c8e0a2b4d6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TRACE_COLUMNS = ("reasoning_tokens", "cache_read_tokens", "cache_write_tokens", "calls")


def _columns(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    existing = _columns("agent_trace_logs")
    with op.batch_alter_table("agent_trace_logs") as batch:
        for name in _TRACE_COLUMNS:
            if name not in existing:
                batch.add_column(sa.Column(name, sa.Integer(), nullable=True))
    if "usage_by_agent" not in _columns("session_stats"):
        with op.batch_alter_table("session_stats") as batch:
            batch.add_column(
                sa.Column("usage_by_agent", sa.JSON(), nullable=False, server_default="{}"),
            )


def downgrade() -> None:
    with op.batch_alter_table("session_stats") as batch:
        batch.drop_column("usage_by_agent")
    with op.batch_alter_table("agent_trace_logs") as batch:
        for name in reversed(_TRACE_COLUMNS):
            batch.drop_column(name)
