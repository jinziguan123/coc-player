"""event_logs.embedding：本局事件原文的向量索引，供 KP 回捞早期剧情

在此之前，KP 对早期剧情的记忆是**纯前向有损**的：老事件被滚动摘要浓缩掉，游标只前进
不后退，浓缩时丢掉的细节永远回不来。事件原文一直在库里，但上下文里没有任何通路把它
取回来——RAG 只覆盖规则书与模组原文，不覆盖「本局已经发生过的事」。

加上这一列后，长局的记忆结构从「有损管道」变成「有损缓存 + 无损底座」：摘要照常浓缩，
但玩家提起当铺老板三章前说的暗号时，KP 能用 recall_history 把原话查回来。

embedding 为 NULL = 尚未索引（存量事件与刚落库的新事件都是这个状态，由 housekeeping
批量补齐）。不做数据迁移：存量会话在下一次浓缩窗口自然补上索引。

Revision ID: e4b9c72a6d13
Revises: d3a8b61f5c92
Create Date: 2026-08-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e4b9c72a6d13"
down_revision: Union[str, Sequence[str], None] = "d3a8b61f5c92"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("event_logs")}


def upgrade() -> None:
    # 幂等：版本号可能因历史漂移被回拨到本迁移之前，而列其实已经建好
    # （见 tests/test_migrations.py 的 identity_schema_repair 用例）。
    if "embedding" in _columns():
        return
    # float32 BLOB，与规则书 / 模组原文的向量存法一致（同一套 numpy 暴力余弦检索）。
    op.add_column("event_logs", sa.Column("embedding", sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    if "embedding" in _columns():
        op.drop_column("event_logs", "embedding")
