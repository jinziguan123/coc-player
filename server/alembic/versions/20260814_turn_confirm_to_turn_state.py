"""回合锁拆表：world_state.turn_confirm → 既有 turn_state 列

把回合推进确认（谁已点「推进本回合」）从 world_state 搬到 GameSession 上早已存在、
却一直未被使用的 turn_state 列——「回合锁」本就是 ADR-003 决策第 4 条点名的强一致状态，
单独成列后不再与剧情记忆整段回写。

turn_state 列此前是空壳（无任何读写），本迁移只做数据搬运 + 移除 world_state.turn_confirm。

Revision ID: e5c2b8f1a4d9
Revises: d3a5f7e9c1b8
Create Date: 2026-08-14
"""
from typing import Sequence, Union

import json

import sqlalchemy as sa
from alembic import op

revision: str = "e5c2b8f1a4d9"
down_revision: Union[str, Sequence[str], None] = "d3a5f7e9c1b8"
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
    rows = bind.execute(
        sa.text("SELECT id, world_state FROM game_sessions")
    ).fetchall()
    for sid, ws_raw in rows:
        ws = _load(ws_raw)
        if "turn_confirm" not in ws:
            continue
        tc = ws.pop("turn_confirm")
        bind.execute(
            sa.text(
                "UPDATE game_sessions SET world_state = :ws, turn_state = :ts "
                "WHERE id = :sid"
            ),
            {"sid": sid, "ws": json.dumps(ws), "ts": json.dumps(tc or {})},
        )


def downgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, turn_state FROM game_sessions "
            "WHERE turn_state IS NOT NULL"
        )
    ).fetchall()
    for sid, ts_raw in rows:
        ts = _load(ts_raw)
        if not ts:
            continue
        ws_raw = bind.execute(
            sa.text("SELECT world_state FROM game_sessions WHERE id = :sid"),
            {"sid": sid},
        ).scalar()
        ws = _load(ws_raw)
        ws["turn_confirm"] = ts
        bind.execute(
            sa.text(
                "UPDATE game_sessions SET world_state = :ws, turn_state = NULL "
                "WHERE id = :sid"
            ),
            {"sid": sid, "ws": json.dumps(ws)},
        )
