"""战报拆表：world_state.recaps → session_recaps（1:N）

把 world_state 里 append-only 的战报列表拆成 1:N 的 session_recaps 表（每条战报一行，
ordinal 记追加顺序）。world_state 收敛为剧情记忆容器（ADR-003）。

升级：建表 → 回填存量 world_state.recaps（每条一行）→ 从 world_state 删除 recaps 键。
降级：按 session 归并回 world_state.recaps → 删表。

Revision ID: d3a5f7e9c1b8
Revises: c7b1e9d3a5f2
Create Date: 2026-08-14
"""
from typing import Sequence, Union

import json
import uuid

import sqlalchemy as sa
from alembic import op

revision: str = "d3a5f7e9c1b8"
down_revision: Union[str, Sequence[str], None] = "c7b1e9d3a5f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _load(raw) -> dict:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    return json.loads(raw)


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("session_recaps"):
        op.create_table(
            "session_recaps",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column(
                "session_id",
                sa.String(),
                sa.ForeignKey("game_sessions.id"),
                nullable=False,
            ),
            sa.Column("ordinal", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("entry", sa.JSON(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=False,
            ),
        )
        op.create_index(
            "ix_session_recaps_session_id", "session_recaps", ["session_id"]
        )

    rows = bind.execute(
        sa.text("SELECT id, world_state FROM game_sessions")
    ).fetchall()
    for sid, ws_raw in rows:
        ws = _load(ws_raw)
        recaps = ws.pop("recaps", None)
        if not recaps:
            continue
        for ordinal, entry in enumerate(recaps):
            bind.execute(
                sa.text(
                    "INSERT INTO session_recaps (id, session_id, ordinal, entry) "
                    "VALUES (:id, :sid, :ord, :entry)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "sid": sid,
                    "ord": ordinal,
                    "entry": json.dumps(entry),
                },
            )
        bind.execute(
            sa.text(
                "UPDATE game_sessions SET world_state = :ws WHERE id = :sid"
            ),
            {"sid": sid, "ws": json.dumps(ws)},
        )


def downgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT session_id, entry FROM session_recaps "
            "ORDER BY session_id, ordinal"
        )
    ).fetchall()
    by_session: dict[str, list] = {}
    for sid, entry_raw in rows:
        by_session.setdefault(sid, []).append(_load(entry_raw))
    for sid, entries in by_session.items():
        ws_raw = bind.execute(
            sa.text("SELECT world_state FROM game_sessions WHERE id = :sid"),
            {"sid": sid},
        ).scalar()
        ws = _load(ws_raw)
        ws["recaps"] = entries
        bind.execute(
            sa.text(
                "UPDATE game_sessions SET world_state = :ws WHERE id = :sid"
            ),
            {"sid": sid, "ws": json.dumps(ws)},
        )
    op.drop_table("session_recaps")
