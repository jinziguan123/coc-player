"""模组 v3：事件独立成表字段 + 结构校验结果

modules 增加 schema_version / events / validation：
- schema_version 标记模组结构版本（1=旧结构，3=事件-条件-效果模型）；
- events 承载剧情事件（kind=event 的场景从 scenes 里独立出来），带可执行效果；
- validation 存解析期结构校验的 errors/warnings，供解析报告与时间线视图展示。

存量模组保持 schema_version=1、events 空数组；读侧按版本兼容旧 scenes。

Revision ID: b8d2f4a6c1e3
Revises: f3b5d7f9a1c2
Create Date: 2026-11-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b8d2f4a6c1e3"
down_revision: Union[str, Sequence[str], None] = "f3b5d7f9a1c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    columns = _columns("modules")
    if "schema_version" not in columns:
        with op.batch_alter_table("modules") as batch:
            batch.add_column(
                sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
            )
    if "events" not in columns:
        with op.batch_alter_table("modules") as batch:
            batch.add_column(sa.Column("events", sa.JSON(), nullable=True))
        op.execute("UPDATE modules SET events = '[]' WHERE events IS NULL")
    if "validation" not in columns:
        with op.batch_alter_table("modules") as batch:
            batch.add_column(sa.Column("validation", sa.JSON(), nullable=True))
        op.execute("UPDATE modules SET validation = '{}' WHERE validation IS NULL")


def downgrade() -> None:
    with op.batch_alter_table("modules") as batch:
        batch.drop_column("validation")
        batch.drop_column("events")
        batch.drop_column("schema_version")
