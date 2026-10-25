"""自动检定结果的存取与作废；不驱动收益兑现或回合编排。"""
from sqlalchemy.orm import Session
from app.domain.checks import normalize_skill, skill_matches
from app.models.event_log import EventLog
from app.models.session import GameSession

AUTO_CHECKS_KEY = "auto_checks"


def clear(game_session: GameSession) -> bool:
    state = dict(game_session.turn_state or {})
    if AUTO_CHECKS_KEY not in state:
        return False
    state.pop(AUTO_CHECKS_KEY)
    game_session.turn_state = state
    return True


def record(game_session: GameSession, *, event_id: str, skill: str, succeeded: bool) -> None:
    state = dict(game_session.turn_state or {})
    state[AUTO_CHECKS_KEY] = [*(state.get(AUTO_CHECKS_KEY) or []),
                              {"event_id": event_id, "skill": skill, "succeeded": bool(succeeded)}]
    game_session.turn_state = state


def auto_outcome(db: Session, game_session: GameSession, skill: str) -> bool | None:
    """本回合系统自动掷过的匹配检定的成败；没有（或其骰子事件已随重生成回滚）返回 None。"""
    records = list((game_session.turn_state or {}).get(AUTO_CHECKS_KEY) or [])
    if not records or not normalize_skill(skill):
        return None
    ids = [r.get("event_id") for r in records if r.get("event_id")]
    alive = {
        row[0] for row in db.query(EventLog.id).filter(EventLog.id.in_(ids)).all()
    } if ids else set()
    for record in reversed(records):
        if record.get("event_id") in alive and skill_matches(skill, record.get("skill", "")):
            return bool(record.get("succeeded"))
    return None

