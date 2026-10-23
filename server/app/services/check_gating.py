"""检定门控收益的生命周期：「先检定、后发货/记账」的暂存只属于发起它的那个玩家回合。

- 每个玩家发起的新回合开始时作废上一轮的暂存（物品、线索）与自动检定记录，
  旧暂存不会被之后某次不相干的投骰兑现；
- 玩家手动投骰仍由 ``planned_effects.settle_pending_*`` 兑现；
- 系统自动掷的检定（AI 队友、暗投）此前没有兑现入口：这里按技能名匹配暂存并立即兑现，
  同时记下结果，供叙事后才暂存物品的守卫直接按已知成败处理。

结果只记在 turn_state（不广播），暗投成败不会经事件元数据泄露给玩家。
"""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.models.event_log import EventLog
from app.models.session import GameSession

AUTO_CHECKS_KEY = "auto_checks"


def _norm(skill: str) -> str:
    return re.sub(r"\s+", "", str(skill or "")).lower()


def skill_matches(expected: str, rolled: str) -> bool:
    a, b = _norm(expected), _norm(rolled)
    return bool(a and b) and (a in b or b in a)


def begin_turn(db: Session, game_session: GameSession) -> bool:
    """新的玩家回合：作废上一轮的门控暂存（物品、线索、移动）与自动检定记录。返回是否有改动。"""
    from app.services import movement   # 延迟导入：movement 依赖本模块

    moved = movement.begin_turn(game_session)
    ts = dict(game_session.turn_state or {})
    ws = dict(game_session.world_state or {})
    changed = False
    if ts.get("pending_item_gains"):
        ts["pending_item_gains"] = []
        changed = True
    if ts.pop(AUTO_CHECKS_KEY, None):
        changed = True
    if ws.pop("pending_clue_reveals", None):
        changed = True
    if changed or moved:
        game_session.turn_state = ts
        game_session.world_state = ws
        db.commit()
    return changed


def auto_outcome(db: Session, game_session: GameSession, skill: str) -> bool | None:
    """本回合系统自动掷过的匹配检定的成败；没有（或其骰子事件已随重生成回滚）返回 None。"""
    records = list((game_session.turn_state or {}).get(AUTO_CHECKS_KEY) or [])
    if not records or not _norm(skill):
        return None
    ids = [r.get("event_id") for r in records if r.get("event_id")]
    alive = {
        row[0] for row in db.query(EventLog.id).filter(EventLog.id.in_(ids)).all()
    } if ids else set()
    for record in reversed(records):
        if record.get("event_id") in alive and skill_matches(skill, record.get("skill", "")):
            return bool(record.get("succeeded"))
    return None


def record_auto_check(
    db: Session, session_id: str, game_session: GameSession, *,
    event_id: str, skill: str, succeeded: bool, module=None, on_first_clue=None,
) -> list:
    """记录一次玩家方的自动检定，并兑现技能匹配的门控暂存（物品、线索）。返回要广播的 chunk。

    门控移动不在这里结算：它按掷骰者本人的成败逐人结算（见 turn_effects._auto_roll_check），
    群检传进来的是「任一成功」汇总，不能替每个人决定能不能过去。
    """
    from app.services import planned_effects   # 延迟导入：planned_effects 依赖 turn_effects

    ts = dict(game_session.turn_state or {})
    ts[AUTO_CHECKS_KEY] = [
        *(ts.get(AUTO_CHECKS_KEY) or []),
        {"event_id": event_id, "skill": skill, "succeeded": bool(succeeded)},
    ]
    game_session.turn_state = ts
    db.commit()

    chunks: list = []
    gains = list(ts.get("pending_item_gains") or [])
    if gains and all(skill_matches(g.get("skill", ""), skill) for g in gains):
        chunks.extend(planned_effects.settle_pending_item_gains(
            db, session_id, game_session, succeeded,
        ))
    staged = (game_session.world_state or {}).get("pending_clue_reveals") or {}
    if staged and skill_matches(staged.get("skill", ""), skill):
        planned_effects.settle_pending_clue_reveals(
            db, session_id, game_session, succeeded, module=module, on_first_clue=on_first_clue,
        )
    return chunks
