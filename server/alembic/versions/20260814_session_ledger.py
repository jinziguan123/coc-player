"""进度台账拆表：world_state.san_checked / scene_events_seen → session_ledger

把会话级的「幂等台账」（谁对哪些恐怖源做过理智检定、哪些场景机制点已触发）搬到 1:1 的
session_ledger 表，不再与剧情记忆整段回写（ADR-003）。

升级：建表 → 回填两键 → 从 world_state 删除这两键（保留其它键）。
降级：把两列写回 world_state → 删表。

Revision ID: b2d4f6a8c0e1
Revises: a1c3e5f7b9d2
Create Date: 2026-08-14
"""
from typing import Sequence, Union

import json

import sqlalchemy as sa
from alembic import op

revision: str = "b2d4f6a8c0e1"
down_revision: Union[str, Sequence[str], None] = "a1c3e5f7b9d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_KEYS = ("san_checked", "scene_events_seen")


def _load(raw):
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    return json.loads(raw)


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("session_ledger"):
        op.create_table(
            "session_ledger",
            sa.Column(
                "session_id",
                sa.String(),
                sa.ForeignKey("game_sessions.id"),
                primary_key=True,
            ),
            sa.Column("san_checked", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("scene_events_seen", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
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

    rows = bind.execute(
        sa.text("SELECT id, world_state FROM game_sessions")
    ).fetchall()
    for sid, ws_raw in rows:
        ws = _load(ws_raw) or {}
        moved = {k: ws.pop(k) for k in _KEYS if k in ws}
        if not moved:
            continue
        bind.execute(
            sa.text(
                "INSERT INTO session_ledger "
                "(session_id, san_checked, scene_events_seen, version) "
                "VALUES (:sid, :sc, :ses, 1)"
            ),
            {
                "sid": sid,
                "sc": json.dumps(moved.get("san_checked") or []),
                "ses": json.dumps(moved.get("scene_events_seen") or {}),
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
            "SELECT session_id, san_checked, scene_events_seen FROM session_ledger"
        )
    ).fetchall()
    for sid, sc_raw, ses_raw in rows:
        ws_raw = bind.execute(
            sa.text("SELECT world_state FROM game_sessions WHERE id = :sid"),
            {"sid": sid},
        ).scalar()
        ws = _load(ws_raw) or {}
        sc = _load(sc_raw)
        ses = _load(ses_raw)
        if sc:
            ws["san_checked"] = sc
        if ses:
            ws["scene_events_seen"] = ses
        bind.execute(
            sa.text(
                "UPDATE game_sessions SET world_state = :ws WHERE id = :sid"
            ),
            {"sid": sid, "ws": json.dumps(ws)},
        )
    op.drop_table("session_ledger")
