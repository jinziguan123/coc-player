"""村规总开关：rule_system_options.enabled

此前只要在规则书页面动过任何一项，村规就一直生效，没有「先照原文跑一局试试」的退路——
想临时回到规则原文，只能把每一项手动改回默认再改回来。加一个总开关，关掉时
``village_options`` 与 ``table_notes`` 一律按「没配过」返回。

默认 **开**：存量用户的村规此刻正在生效（实测有存档开着幸运消费在跑），
升级不该让他们的配置突然失效。

Revision ID: f7b2d4e6a8c1
Revises: e5a7c9b1d3f4
Create Date: 2026-08-31
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f7b2d4e6a8c1"
down_revision: Union[str, Sequence[str], None] = "e5a7c9b1d3f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {c["name"] for c in inspector.get_columns("rule_system_options")}
    if "enabled" not in existing:
        op.add_column(
            "rule_system_options",
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        )


def downgrade() -> None:
    op.drop_column("rule_system_options", "enabled")
