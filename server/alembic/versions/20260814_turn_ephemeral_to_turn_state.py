"""回合级待结算态拆表：pending_checks / pending_item_gains / item_delta_keys → turn_state

把 world_state 里三个回合级待结算键（待投骰检定、待检定收益暂存、物品增减幂等键）搬到
GameSession.turn_state 列，与已迁入的回合锁 turn_confirm 同处——它们都是「回合流转」状态，
单独成列后不再与剧情记忆整段回写（ADR-003 决策第 4 条「回合锁」的完整收口）。

pending_clue_reveals 刻意不迁：它经 world_memory.stage_clue_reveal（走 _apply_world_memory）
与 clue_ledger 同生同灭，切分会把「先检定后记账」的线索流撕成两列，得不偿失。

Revision ID: f7d3a9c5e1b2
Revises: e5c2b8f1a4d9
Create Date: 2026-08-14
"""
from typing import Sequence, Union

import json

import sqlalchemy as sa
from alembic import op

revision: str = "f7d3a9c5e1b2"
down_revision: Union[str, Sequence[str], None] = "e5c2b8f1a4d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_KEYS = ("pending_checks", "pending_item_gains", "item_delta_keys")


def _load(raw) -> dict:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    return json.loads(raw)


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, world_state, turn_state FROM game_sessions")
    ).fetchall()
    for sid, ws_raw, ts_raw in rows:
        ws = _load(ws_raw)
        moved = {k: ws.pop(k) for k in _KEYS if k in ws}
        if not moved:
            continue
        # 与已迁入的 turn_confirm 合并（turn_state 此前只放回合锁）
        ts = _load(ts_raw)
        ts.update(moved)
        bind.execute(
            sa.text(
                "UPDATE game_sessions SET world_state = :ws, turn_state = :ts "
                "WHERE id = :sid"
            ),
            {"sid": sid, "ws": json.dumps(ws), "ts": json.dumps(ts)},
        )


def downgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, world_state, turn_state FROM game_sessions "
            "WHERE turn_state IS NOT NULL"
        )
    ).fetchall()
    for sid, ws_raw, ts_raw in rows:
        ws = _load(ws_raw)
        ts = _load(ts_raw)
        moved = {k: ts.pop(k) for k in _KEYS if k in ts}
        if not moved:
            continue
        ws.update(moved)
        bind.execute(
            sa.text(
                "UPDATE game_sessions SET world_state = :ws, turn_state = :ts "
                "WHERE id = :sid"
            ),
            {"sid": sid, "ws": json.dumps(ws), "ts": json.dumps(ts)},
        )
