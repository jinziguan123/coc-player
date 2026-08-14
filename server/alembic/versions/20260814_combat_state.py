"""战斗态拆表：world_state.combat → combat_states

把 GameSession.world_state 里高频强一致的活跃战斗状态机（combat）搬到独立表
combat_states（1:1，session_id 即主键）。world_state 收敛为剧情记忆容器（ADR-003）。

升级：建表 → 回填存量 world_state.combat → 从 world_state 删除 combat 键（保留其它键）。
降级：把 combat_states.state 写回 world_state.combat → 删表。

Revision ID: f8a4c1e9b2d7
Revises: f5c1d83b7e24
Create Date: 2026-08-14
"""
from typing import Sequence, Union

import json

import sqlalchemy as sa
from alembic import op

revision: str = "f8a4c1e9b2d7"
down_revision: Union[str, Sequence[str], None] = "f5c1d83b7e24"
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
    # 幂等：版本号可能因历史漂移被回拨到本迁移之前，而表其实已经建好
    # （见 tests/test_migrations.py 的 identity_schema_repair 用例）。
    if not sa.inspect(bind).has_table("combat_states"):
        op.create_table(
            "combat_states",
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
        combat = ws.pop("combat", None)
        if combat is None:
            continue
        bind.execute(
            sa.text(
                "INSERT INTO combat_states (session_id, state, version) "
                "VALUES (:sid, :state, 1)"
            ),
            {"sid": sid, "state": json.dumps(combat)},
        )
        # 只在真的移除了 combat 键时写回，避免无谓 UPDATE 与键序扰动。
        bind.execute(
            sa.text(
                "UPDATE game_sessions SET world_state = :ws WHERE id = :sid"
            ),
            {"sid": sid, "ws": json.dumps(ws)},
        )


def downgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT session_id, state FROM combat_states")
    ).fetchall()
    for sid, state_raw in rows:
        state = _load(state_raw)
        ws_raw = bind.execute(
            sa.text("SELECT world_state FROM game_sessions WHERE id = :sid"),
            {"sid": sid},
        ).scalar()
        ws = _load(ws_raw)
        ws["combat"] = state
        bind.execute(
            sa.text(
                "UPDATE game_sessions SET world_state = :ws WHERE id = :sid"
            ),
            {"sid": sid, "ws": json.dumps(ws)},
        )
    op.drop_table("combat_states")
