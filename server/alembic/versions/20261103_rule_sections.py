"""规则书按词条建索引：rule_sections 表、子块归属词条、保存原件与切块版本

rule_sections：按字号识别出的词条（小节）全文与路径，检索命中子块后返回整条词条；
rule_chunks.section_id：子块所属词条（存量旧版切块为 NULL，检索时按旧方式返回块本身）；
rulebooks.source_file / chunker_version：本机保存的 PDF 原件与建索引所用的切块版本，
切块逻辑升级后有原件的规则书可在后台自动重建。

Revision ID: f3b5d7f9a1c2
Revises: e2a4c6e8f0b1
Create Date: 2026-11-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f3b5d7f9a1c2"
down_revision: Union[str, Sequence[str], None] = "e2a4c6e8f0b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "rule_sections" not in _tables():
        op.create_table(
            "rule_sections",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("rulebook_id", sa.String(), nullable=False),
            sa.Column("ordinal", sa.Integer(), nullable=False),
            sa.Column("level", sa.Integer(), nullable=False),
            sa.Column("title", sa.String(), nullable=False),
            sa.Column("path", sa.Text(), nullable=False),
            sa.Column("page_start", sa.Integer(), nullable=False),
            sa.Column("page_end", sa.Integer(), nullable=False),
            sa.Column("text", sa.Text(), nullable=False),
            sa.ForeignKeyConstraint(["rulebook_id"], ["rulebooks.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_rule_sections_rulebook_id", "rule_sections", ["rulebook_id"])
    if "section_id" not in _columns("rule_chunks"):
        with op.batch_alter_table("rule_chunks") as batch:
            batch.add_column(sa.Column("section_id", sa.String(), nullable=True))
            batch.create_index("ix_rule_chunks_section_id", ["section_id"])
            batch.create_foreign_key(
                "fk_rule_chunks_section_id", "rule_sections", ["section_id"], ["id"], ondelete="CASCADE",
            )
    existing = _columns("rulebooks")
    with op.batch_alter_table("rulebooks") as batch:
        if "source_file" not in existing:
            batch.add_column(sa.Column("source_file", sa.String(), nullable=False, server_default=""))
        if "chunker_version" not in existing:
            batch.add_column(sa.Column("chunker_version", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("rulebooks") as batch:
        batch.drop_column("chunker_version")
        batch.drop_column("source_file")
    with op.batch_alter_table("rule_chunks") as batch:
        batch.drop_constraint("fk_rule_chunks_section_id", type_="foreignkey")
        batch.drop_index("ix_rule_chunks_section_id")
        batch.drop_column("section_id")
    op.drop_index("ix_rule_sections_rulebook_id", table_name="rule_sections")
    op.drop_table("rule_sections")
