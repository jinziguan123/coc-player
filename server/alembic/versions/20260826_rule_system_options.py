"""村规按规则系统落表：rule_system_options

村规是这一桌长期沿用的规矩，不是一局一设的东西——所以从会话搬到规则系统这一层，
在规则书页面配置。会话上那一列保留为「本局覆盖」，目前没有入口，但层级仍是
模组默认 → 村规 → 本局覆盖。

Revision ID: d4f6b8c0e2a3
Revises: c3e5a7b9d1f2
Create Date: 2026-08-26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4f6b8c0e2a3"
down_revision: Union[str, Sequence[str], None] = "c3e5a7b9d1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("rule_system_options"):
        return
    op.create_table(
        "rule_system_options",
        sa.Column("rule_system", sa.String(), primary_key=True),
        sa.Column("options", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at", sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("rule_system_options")
