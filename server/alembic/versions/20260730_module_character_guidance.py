"""modules.character_guidance：按模组设定给出的车卡建议

玩家针对某个模组建卡时需要知道「这个本子想要什么样的调查员」——时代、地域、
职业取向、必要技能、不合适的类型。这些完全由模组设定派生，一次生成即可长期使用，
所以落在模组上而不是每次建卡时现算。

Revision ID: b2d5f8a31c47
Revises: a1c4e7f20d31
Create Date: 2026-07-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b2d5f8a31c47"
down_revision: Union[str, Sequence[str], None] = "a1c4e7f20d31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("modules")}


def upgrade() -> None:
    # 幂等：兼容仓库既有的「版本号回退后重放」修复场景。
    if "character_guidance" not in _columns():
        op.add_column(
            "modules",
            sa.Column("character_guidance", sa.JSON(), nullable=False, server_default="{}"),
        )


def downgrade() -> None:
    if "character_guidance" in _columns():
        op.drop_column("modules", "character_guidance")
