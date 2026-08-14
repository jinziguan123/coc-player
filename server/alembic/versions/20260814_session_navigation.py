"""导航态拆表：world_state.party_locations / visited_scenes → session_navigation

把队伍的「位置/导航」状态（各角色所在场景、真正到访过的场景）搬到 1:1 的
session_navigation 表，不再与剧情记忆整段回写（ADR-003）。

升级：建表 → 回填两键 → 从 world_state 删除这两键（保留其它键）。
降级：把两列写回 world_state → 删表。

Revision ID: a1c3e5f7b9d2
Revises: f7d3a9c5e1b2
Create Date: 2026-08-14
"""
from typing import Sequence, Union

import json

import sqlalchemy as sa
from alembic import op

revision: str = "a1c3e5f7b9d2"
down_revision: Union[str, Sequence[str], None] = "f7d3a9c5e1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_KEYS = ("party_locations", "visited_scenes")


def _load(raw):
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    return json.loads(raw)


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("session_navigation"):
        op.create_table(
            "session_navigation",
            sa.Column(
                "session_id",
                sa.String(),
                sa.ForeignKey("game_sessions.id"),
                primary_key=True,
            ),
            sa.Column("party_locations", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("visited_scenes", sa.JSON(), nullable=False, server_default="[]"),
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
                "INSERT INTO session_navigation "
                "(session_id, party_locations, visited_scenes, version) "
                "VALUES (:sid, :pl, :vs, 1)"
            ),
            {
                "sid": sid,
                "pl": json.dumps(moved.get("party_locations") or {}),
                "vs": json.dumps(moved.get("visited_scenes") or []),
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
            "SELECT session_id, party_locations, visited_scenes "
            "FROM session_navigation"
        )
    ).fetchall()
    for sid, pl_raw, vs_raw in rows:
        ws_raw = bind.execute(
            sa.text("SELECT world_state FROM game_sessions WHERE id = :sid"),
            {"sid": sid},
        ).scalar()
        ws = _load(ws_raw) or {}
        pl = _load(pl_raw)
        vs = _load(vs_raw)
        if pl:
            ws["party_locations"] = pl
        if vs:
            ws["visited_scenes"] = vs
        bind.execute(
            sa.text(
                "UPDATE game_sessions SET world_state = :ws WHERE id = :sid"
            ),
            {"sid": sid, "ws": json.dumps(ws)},
        )
    op.drop_table("session_navigation")
