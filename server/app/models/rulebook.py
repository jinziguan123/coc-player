from sqlalchemy import JSON, ForeignKey, Index, Integer, LargeBinary, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class Rulebook(Base, UUIDMixin, TimestampMixin):
    """已入库的规则书（一份 PDF = 一条记录）。"""

    __tablename__ = "rulebooks"

    title: Mapped[str] = mapped_column()
    rule_system: Mapped[str] = mapped_column(default="coc")
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    # indexing（建索引中）/ ready（可检索）/ failed（失败）
    status: Mapped[str] = mapped_column(default="indexing")
    embed_model: Mapped[str] = mapped_column(default="")
    error: Mapped[str] = mapped_column(Text, default="")


class RuleChunk(Base, UUIDMixin):
    """规则书切块 + 嵌入向量（float32 原始字节存 BLOB）。"""

    __tablename__ = "rule_chunks"

    rulebook_id: Mapped[str] = mapped_column(
        ForeignKey("rulebooks.id", ondelete="CASCADE"), index=True
    )
    # 冗余 rule_system 便于按规则系统直接过滤检索，省一次 join
    rule_system: Mapped[str] = mapped_column(default="coc", index=True)
    page: Mapped[int] = mapped_column(Integer, default=0)
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[bytes] = mapped_column(LargeBinary)


Index("ix_rule_chunks_book_ord", RuleChunk.rulebook_id, RuleChunk.ordinal)


class RuleSystemOptions(Base, TimestampMixin):
    """一套规则系统的**村规**（这一桌长期沿用的改动），按 rule_system 存一行。

    村规是桌上的规矩，不是一局一设的东西——所以它挂在规则系统上、在规则书页面配置，
    而不是每开一局在房间里重填一遍。只存与规则原文不同的项，``{}`` = 全照原文。
    """

    __tablename__ = "rule_system_options"

    rule_system: Mapped[str] = mapped_column(primary_key=True)
    options: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
