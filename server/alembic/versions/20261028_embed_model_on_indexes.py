"""模组原文索引与事件索引记录嵌入模型

modules 增加 rag_embed_model（建原文索引用的模型）；event_logs 增加 embed_model（每条事件向量
用的模型，事件是增量索引的，所以逐条记）。存量向量都是默认模型 bge-small-zh-v1.5 建的，据此回填。
切换嵌入模型后据此识别旧索引：查询必须用建索引的同一个模型，旧向量不能拿新模型去查。

Revision ID: e2a4c6e8f0b1
Revises: d9f1b3c5e7a9
Create Date: 2026-10-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e2a4c6e8f0b1"
down_revision: Union[str, Sequence[str], None] = "d9f1b3c5e7a9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_LEGACY_MODEL = "BAAI/bge-small-zh-v1.5"


def _columns(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "rag_embed_model" not in _columns("modules"):
        with op.batch_alter_table("modules") as batch:
            batch.add_column(
                sa.Column("rag_embed_model", sa.String(), nullable=False, server_default=""),
            )
        op.execute(
            sa.text("UPDATE modules SET rag_embed_model = :m WHERE rag_status = 'ready'")
            .bindparams(m=_LEGACY_MODEL)
        )
    if "embed_model" not in _columns("event_logs"):
        with op.batch_alter_table("event_logs") as batch:
            batch.add_column(sa.Column("embed_model", sa.String(), nullable=True))
        op.execute(
            sa.text("UPDATE event_logs SET embed_model = :m WHERE embedding IS NOT NULL")
            .bindparams(m=_LEGACY_MODEL)
        )


def downgrade() -> None:
    with op.batch_alter_table("event_logs") as batch:
        batch.drop_column("embed_model")
    with op.batch_alter_table("modules") as batch:
        batch.drop_column("rag_embed_model")
