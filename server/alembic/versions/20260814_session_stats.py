"""运行统计拆表：world_state.session_usage / turn_usage / rag_stats → session_stats

把 GameSession.world_state 里 append-only 的「运行统计」（token 用量、RAG 检索质量）搬到
独立表 session_stats（1:1，session_id 即主键）。world_state 收敛为剧情记忆容器（ADR-003）。

预算校准系数 budget_scale 刻意不迁（被纯函数 build_kp_context 每轮读取），仍在 world_state。

升级：建表 → 回填三个键 → 从 world_state 删除这三个键（保留其它键）。
降级：把三列写回 world_state → 删表。

Revision ID: c7b1e9d3a5f2
Revises: a9e2d4c7b1f6
Create Date: 2026-08-14
"""
from typing import Sequence, Union

import json

import sqlalchemy as sa
from alembic import op

revision: str = "c7b1e9d3a5f2"
down_revision: Union[str, Sequence[str], None] = "a9e2d4c7b1f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_KEYS = ("session_usage", "turn_usage", "rag_stats")


def _load(raw) -> dict:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    return json.loads(raw)


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("session_stats"):
        op.create_table(
            "session_stats",
            sa.Column(
                "session_id",
                sa.String(),
                sa.ForeignKey("game_sessions.id"),
                primary_key=True,
            ),
            sa.Column("session_usage", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("turn_usage", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("rag_stats", sa.JSON(), nullable=False, server_default="{}"),
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
        moved = {k: ws.pop(k) for k in _KEYS if k in ws}
        if not moved:
            continue
        bind.execute(
            sa.text(
                "INSERT INTO session_stats "
                "(session_id, session_usage, turn_usage, rag_stats, version) "
                "VALUES (:sid, :su, :tu, :rs, 1)"
            ),
            {
                "sid": sid,
                "su": json.dumps(moved.get("session_usage") or {}),
                "tu": json.dumps(moved.get("turn_usage") or {}),
                "rs": json.dumps(moved.get("rag_stats") or {}),
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
            "SELECT session_id, session_usage, turn_usage, rag_stats "
            "FROM session_stats"
        )
    ).fetchall()
    for sid, su_raw, tu_raw, rs_raw in rows:
        ws_raw = bind.execute(
            sa.text("SELECT world_state FROM game_sessions WHERE id = :sid"),
            {"sid": sid},
        ).scalar()
        ws = _load(ws_raw)
        for key, blob in (
            ("session_usage", _load(su_raw)),
            ("turn_usage", _load(tu_raw)),
            ("rag_stats", _load(rs_raw)),
        ):
            if blob:
                ws[key] = blob
        bind.execute(
            sa.text(
                "UPDATE game_sessions SET world_state = :ws WHERE id = :sid"
            ),
            {"sid": sid, "ws": json.dumps(ws)},
        )
    op.drop_table("session_stats")
