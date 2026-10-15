"""规则数据表补丁：rule_system_options.data_patches 与 modules.default_rule_data

房主（村规）与模组作者可以改动武器、职业、技能、专精、装备、疯狂症状等数据表。
只存与官方数据的差异（补丁行），默认 ``{}`` = 全照官方数据，存量数据无需迁移。

Revision ID: a3d5f7b9c1e2
Revises: f7c2e4a9b8d1
Create Date: 2026-10-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a3d5f7b9c1e2"
down_revision: Union[str, Sequence[str], None] = "f7c2e4a9b8d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "data_patches" not in _columns("rule_system_options"):
        op.add_column(
            "rule_system_options",
            sa.Column("data_patches", sa.JSON(), nullable=False, server_default="{}"),
        )
    if "default_rule_data" not in _columns("modules"):
        op.add_column(
            "modules",
            sa.Column("default_rule_data", sa.JSON(), nullable=False, server_default="{}"),
        )


def downgrade() -> None:
    op.drop_column("modules", "default_rule_data")
    op.drop_column("rule_system_options", "data_patches")
