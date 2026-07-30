"""characters.origin_character_id：参战副本指回客人库里的原件

联机时客人的角色卡会在房主机器上留一份参战副本（房主的规则引擎要读写它才跑得动）。
本局结束后那份副本上的 HP/SAN/成长/物品才是最新的，得写回客人自己的库——但此前
副本与原件之间毫无关联，同步无从下手。

**刻意不建外键**：原件在客人库里，那个 id 在房主库里根本不存在。它是跨库标识，
不是引用完整性约束。

Revision ID: a1c4e7f20d31
Revises: f7b3c9d1e520
Create Date: 2026-07-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1c4e7f20d31"
down_revision: Union[str, Sequence[str], None] = "f7b3c9d1e520"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_INDEX = "ix_characters_origin_character_id"


def _columns() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("characters")}


def _indexes() -> set[str]:
    return {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("characters")}


def upgrade() -> None:
    # 幂等：支持「版本号被回退、而 schema 已含新列」的修复场景重放本迁移
    # （见 tests/test_migrations.py 的 identity_schema_repair 用例）。
    if "origin_character_id" not in _columns():
        op.add_column("characters", sa.Column("origin_character_id", sa.String(), nullable=True))
    if _INDEX not in _indexes():
        # 客人同步时要按它反查副本，而这张表会随开团次数增长。
        op.create_index(_INDEX, "characters", ["origin_character_id"], unique=False)


def downgrade() -> None:
    if _INDEX in _indexes():
        op.drop_index(_INDEX, table_name="characters")
    if "origin_character_id" in _columns():
        op.drop_column("characters", "origin_character_id")
