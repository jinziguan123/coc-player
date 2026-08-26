"""家规参数：game_sessions.rule_options + modules.default_rule_options

把引擎里写死的裁定阈值（大成功/大失败、奖惩骰上限、重伤与临时疯狂口径、成长开关、
幸运消费）抽成一层**读时覆盖**的配置。两列都只存与 RAW 不同的项，``{}`` = 全默认，
所以存量存档不需要回填任何数据——加列即可。

Revision ID: c3e5a7b9d1f2
Revises: b2d4f6a8c0e1
Create Date: 2026-08-26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3e5a7b9d1f2"
down_revision: Union[str, Sequence[str], None] = "b2d4f6a8c0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = (
    ("game_sessions", "rule_options"),
    ("modules", "default_rule_options"),
)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table, column in _COLUMNS:
        existing = {c["name"] for c in inspector.get_columns(table)}
        if column in existing:
            continue
        op.add_column(
            table,
            sa.Column(column, sa.JSON(), nullable=False, server_default="{}"),
        )


def downgrade() -> None:
    for table, column in _COLUMNS:
        op.drop_column(table, column)
