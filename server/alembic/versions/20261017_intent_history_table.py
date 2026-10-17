"""意图历史独立表 intent_history

Revision ID: c8e0a2b4d6f7
Revises: b4e6f8a0c2d3
Create Date: 2026-10-17
"""
from __future__ import annotations

import json
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c8e0a2b4d6f7"
down_revision: Union[str, Sequence[str], None] = "b4e6f8a0c2d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "intent_history" not in inspector.get_table_names():
        op.create_table(
            "intent_history",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "session_id", sa.String(),
                sa.ForeignKey("game_sessions.id", ondelete="CASCADE"), nullable=False,
            ),
            sa.Column("actor_id", sa.String(64), nullable=False, server_default=""),
            sa.Column("actor_name", sa.String(128), nullable=False, server_default=""),
            sa.Column("source", sa.String(16), nullable=False, server_default=""),
            sa.Column("action", sa.String(32), nullable=False, server_default=""),
            sa.Column("target", sa.String(256), nullable=False, server_default=""),
            sa.Column("verb", sa.String(32), nullable=False, server_default=""),
            sa.Column("content", sa.Text(), nullable=False, server_default=""),
            sa.Column("observes_seq", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("target_version", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("precondition_ok", sa.Boolean(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_intent_history_session_id", "intent_history", ["session_id"])
        op.create_index("ix_intent_history_source", "intent_history", ["source"])
    _backfill()


def _backfill() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT id, world_state FROM game_sessions WHERE world_state IS NOT NULL"
    )).fetchall()
    for session_id, raw in rows:
        try:
            ws = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except Exception:
            continue
        items = ws.get("intent_history") if isinstance(ws, dict) else None
        if not isinstance(items, list) or not items:
            continue
        for item in items[-200:]:
            if not isinstance(item, dict):
                continue
            bind.execute(sa.text(
                "INSERT INTO intent_history "
                "(id, session_id, actor_id, actor_name, source, action, target, verb, content, "
                "observes_seq, target_version, precondition_ok) "
                "VALUES (:id, :sid, :aid, :an, :src, :act, :target, :verb, :content, "
                ":seq, :ver, :ok)"
            ), {
                "id": str(uuid.uuid4()), "sid": session_id,
                "aid": str(item.get("actor_id") or ""), "an": str(item.get("actor_name") or ""),
                "src": str(item.get("source") or ""), "act": str(item.get("action") or ""),
                "target": str(item.get("target") or ""), "verb": str(item.get("verb") or ""),
                "content": str(item.get("content") or ""),
                "seq": int(item.get("observes_seq") or 0),
                "ver": int(item.get("target_version") or 0),
                "ok": bool(item.get("precondition_ok", True)),
            })
        ws.pop("intent_history", None)
        bind.execute(
            sa.text("UPDATE game_sessions SET world_state = :ws WHERE id = :sid"),
            {"ws": json.dumps(ws, ensure_ascii=False), "sid": session_id},
        )


def downgrade() -> None:
    op.drop_index("ix_intent_history_source", table_name="intent_history")
    op.drop_index("ix_intent_history_session_id", table_name="intent_history")
    op.drop_table("intent_history")
