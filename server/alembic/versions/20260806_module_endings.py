"""modules.endings：模组写明的结局分支

此前「结局」只以自然语言存在于场景 events 的备注里（常暗之箱：「加速：结局A；减速：
结局B」），系统无从知晓玩家已经抵达终局，KP 也就永远不会收尾。结构化之后，规划器每轮
拿得到结局条件，命中即落 world_state.ending_reached 并提示玩家收束。

存量模组 endings 为空数组 = 维持原样（不判结局），不需要数据迁移。

Revision ID: d3a8b61f5c92
Revises: c7f1a94b2e60
Create Date: 2026-08-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d3a8b61f5c92"
down_revision: Union[str, Sequence[str], None] = "c7f1a94b2e60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("modules")}


def upgrade() -> None:
    # 幂等：兼容仓库既有的「版本号回退后重放」修复场景。
    if "endings" not in _columns():
        op.add_column(
            "modules",
            sa.Column("endings", sa.JSON(), nullable=False, server_default="[]"),
        )


def downgrade() -> None:
    if "endings" in _columns():
        op.drop_column("modules", "endings")
