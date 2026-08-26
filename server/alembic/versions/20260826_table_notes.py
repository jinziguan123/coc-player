"""桌面约定：rule_system_options.table_notes

参数表达不了的规矩（「本局重调查轻战斗」「NPC 死亡不可逆」）走自由文本，常驻注入 KP
与规划器。它只影响叙述与裁定倾向，不改骰子结算——能改结算的都在 options 里。

Revision ID: e5a7c9b1d3f4
Revises: d4f6b8c0e2a3
Create Date: 2026-08-26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5a7c9b1d3f4"
down_revision: Union[str, Sequence[str], None] = "d4f6b8c0e2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {c["name"] for c in inspector.get_columns("rule_system_options")}
    if "table_notes" not in existing:
        op.add_column(
            "rule_system_options",
            sa.Column("table_notes", sa.Text(), nullable=False, server_default=""),
        )


def downgrade() -> None:
    op.drop_column("rule_system_options", "table_notes")
