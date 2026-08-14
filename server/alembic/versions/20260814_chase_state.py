"""追逐态拆表：world_state.chase → chase_states

把 GameSession.world_state 里高频强一致的活跃追逐状态机（chase）搬到独立表
chase_states（1:1，session_id 即主键）。world_state 收敛为剧情记忆容器（ADR-003）。

升级：建表 → 回填存量 world_state.chase → 从 world_state 删除 chase 键（保留其它键）。
降级：把 chase_states.state 写回 world_state.chase → 删表。

Revision ID: a9e2d4c7b1f6
Revises: f8a4c1e9b2d7
Create Date: 2026-08-14
"""
from typing import Sequence, Union

import json

import sqlalchemy as sa
from alembic import op

revision: str = "a9e2d4c7b1f6"
down_revision: Union[str, Sequence[str], None] = "f8a4c1e9b2d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _load(raw) -> dict:
    """raw 可能是 SQLAlchemy 反序列化好的 dict，也可能是 SQLite 里的 JSON 文本。"""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    return json.loads(raw)


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("chase_states"):
        op.create_table(
            "chase_states",
            sa.Column(
                "session_id",
                sa.String(),
                sa.ForeignKey("game_sessions.id"),
                primary_key=True,
            ),
            sa.Column("state", sa.JSON(), nullable=False),
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
        ws = _load(ws_raw)
        chase = ws.pop("chase", None)
        if chase is None:
            continue
        bind.execute(
            sa.text(
                "INSERT INTO chase_states (session_id, state, version) "
                "VALUES (:sid, :state, 1)"
            ),
            {"sid": sid, "state": json.dumps(chase)},
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
        sa.text("SELECT session_id, state FROM chase_states")
    ).fetchall()
    for sid, state_raw in rows:
        state = _load(state_raw)
        ws_raw = bind.execute(
            sa.text("SELECT world_state FROM game_sessions WHERE id = :sid"),
            {"sid": sid},
        ).scalar()
        ws = _load(ws_raw)
        ws["chase"] = state
        bind.execute(
            sa.text(
                "UPDATE game_sessions SET world_state = :ws WHERE id = :sid"
            ),
            {"sid": sid, "ws": json.dumps(ws)},
        )
    op.drop_table("chase_states")
