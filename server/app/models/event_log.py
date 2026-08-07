from datetime import datetime

from sqlalchemy import (
    JSON,
    Enum,
    ForeignKey,
    LargeBinary,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDMixin


class EventLog(Base, UUIDMixin):
    __tablename__ = "event_logs"

    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "sequence_num",
            name="uq_event_logs_session_sequence",
        ),
    )

    session_id: Mapped[str] = mapped_column(
        ForeignKey("game_sessions.id"), index=True
    )
    sequence_num: Mapped[int] = mapped_column()
    event_type: Mapped[str] = mapped_column(
        Enum(
            "dialogue", "action", "dice", "narration", "system", "ooc",
            name="event_type",
        )
    )
    actor_id: Mapped[str | None] = mapped_column(nullable=True)
    actor_name: Mapped[str] = mapped_column(default="")
    content: Mapped[str] = mapped_column(Text, default="")
    visibility: Mapped[list] = mapped_column(JSON, default=list)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 事件正文的 float32 向量（BLOB），供 recall_history 把被摘要浓缩掉的早期剧情原文
    #: 检索回来。NULL = 尚未索引；由 generation_housekeeping 在浓缩窗口批量补齐。
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
