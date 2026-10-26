"""生成暂停说明持久化；同一生成内复用事件，新生成独立记账。"""
from sqlalchemy.orm import Session

from app.ai import call_budget
from app.models.session import GameSession
from app.services import event_store


def persist_budget_pause(db: Session, session_id: str) -> str | None:
    budget = call_budget.current_budget()
    if budget is not None and session_id in budget.pause_event_ids:
        return budget.pause_event_ids[session_id]
    if db.get(GameSession, session_id) is None:
        return None
    event = event_store.add_event(
        db, session_id, "system", call_budget.BUDGET_PAUSE_NOTICE, actor_name="系统",
        metadata={"kind": "generation_budget_paused"},
    )
    # 只有成功提交后才记凭据；内层写入失败时，外层仍可用新会话重试。
    if budget is not None:
        budget.pause_event_ids[session_id] = event.id
    return event.id
