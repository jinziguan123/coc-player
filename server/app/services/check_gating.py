"""回合门控生命周期编排；状态由各所有者维护，自动检定后驱动收益兑现。"""
from sqlalchemy.orm import Session

from app.domain.checks import skill_matches as skill_matches
from app.models.session import GameSession
from app.services import auto_check_state, movement, planned_effects, world_memory
from app.services.auto_check_state import AUTO_CHECKS_KEY as AUTO_CHECKS_KEY, auto_outcome as auto_outcome


def begin_turn(db: Session, game_session: GameSession) -> bool:
    """作废各所有者的暂存，一次提交；任何一步失败整体回滚。"""
    try:
        moved = movement.begin_turn(game_session)
        items = planned_effects.discard_pending_item_gains(db, game_session, commit=False)
        checks = auto_check_state.clear(game_session)
        world, clues = world_memory.discard_pending_clue_reveals(game_session.world_state)
        if clues:
            game_session.world_state = world
        changed = moved or items or checks or clues
        if changed:
            db.commit()
        return changed
    except BaseException:
        db.rollback()
        raise


def record_auto_check(
    db: Session, session_id: str, game_session: GameSession, *,
    event_id: str, skill: str, succeeded: bool, module=None, on_first_clue=None,
) -> list:
    """保存自动检定，再让收益所有者兑现匹配条目；移动由逐人结算入口处理。"""
    auto_check_state.record(game_session, event_id=event_id, skill=skill, succeeded=succeeded)
    db.commit()
    return planned_effects.settle_auto_rewards(
        db, session_id, game_session, skill=skill, succeeded=succeeded,
        module=module, on_first_clue=on_first_clue,
    )
