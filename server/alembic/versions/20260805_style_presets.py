"""文风 / 画风：模组默认值 + 每局可覆盖

四列都是可空回落语义的字符串（""=未指定），所以存量模组与存量存档不需要数据迁移，
加上列就等于「全部维持原样」。取值约定见 app.services.style_presets。

Revision ID: c7f1a94b2e60
Revises: b2d5f8a31c47
Create Date: 2026-08-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c7f1a94b2e60"
down_revision: Union[str, Sequence[str], None] = "b2d5f8a31c47"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = {
    "modules": ["default_narrative_style", "default_image_style"],
    "game_sessions": ["narrative_style", "image_style"],
}


def _existing(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    # 幂等：兼容仓库既有的「版本号回退后重放」修复场景。
    for table, cols in _COLUMNS.items():
        have = _existing(table)
        for col in cols:
            if col not in have:
                op.add_column(
                    table,
                    sa.Column(col, sa.String(), nullable=False, server_default=""),
                )


def downgrade() -> None:
    for table, cols in _COLUMNS.items():
        have = _existing(table)
        for col in cols:
            if col in have:
                op.drop_column(table, col)
