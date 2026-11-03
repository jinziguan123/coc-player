import sqlalchemy as sa
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
    #: 本机保存的 PDF 原件（数据目录 rulebooks/ 下的文件名）；""＝旧版导入时没存原件
    source_file: Mapped[str] = mapped_column(default="", server_default="")
    #: 建索引所用的切块逻辑版本（rulebook_service.CHUNKER_VERSION）；落后且有原件时后台自动重建
    chunker_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class RuleSection(Base, UUIDMixin):
    """规则书的一个词条（按字号识别的小节）：检索命中子块后，返回给 KP 的是整条词条。"""

    __tablename__ = "rule_sections"

    rulebook_id: Mapped[str] = mapped_column(
        ForeignKey("rulebooks.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    #: 标题层级（0＝章；-1＝第一个标题之前的前言）
    level: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(default="")
    #: 「第七章 追逐 › 追逐轮 › 追逐轮中的行动」
    path: Mapped[str] = mapped_column(Text, default="")
    page_start: Mapped[int] = mapped_column(Integer, default=0)
    page_end: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text)


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
    #: 所属词条；None＝旧版切块（没有词条结构，命中即返回块本身）
    section_id: Mapped[str | None] = mapped_column(
        ForeignKey("rule_sections.id", ondelete="CASCADE"), nullable=True, index=True,
    )


Index("ix_rule_chunks_book_ord", RuleChunk.rulebook_id, RuleChunk.ordinal)


class RuleSystemOptions(Base, TimestampMixin):
    """一套规则系统的**村规**（这一桌长期沿用的改动），按 rule_system 存一行。

    村规是桌上的规矩，不是一局一设的东西——所以它挂在规则系统上、在规则书页面配置，
    而不是每开一局在房间里重填一遍。只存与规则原文不同的项，``{}`` = 全照原文。
    """

    __tablename__ = "rule_system_options"

    rule_system: Mapped[str] = mapped_column(primary_key=True)
    options: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    #: 桌面约定：参数表达不了的那些规矩，自由文本。**只影响叙述与裁定倾向，不改骰子结算**
    #: ——它是喂给 KP 的一段话，不是引擎能执行的东西（能执行的都在 options 里）。
    table_notes: Mapped[str] = mapped_column(Text, default="", server_default="")
    #: 村规总开关。关掉时 options 与 table_notes 一律按「没配过」处理，整桌回到规则原文——
    #: 配置本身留着，随时能开回来，不必把每一项手动改回默认。
    enabled: Mapped[bool] = mapped_column(default=True, server_default=sa.true())
    #: 数据表补丁（表名 → 补丁行列表），只存与官方数据的差异；同受总开关管。见 app.rules.data。
    data_patches: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
