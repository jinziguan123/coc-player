"""真人 KP 的待办台：待投检定、玩家的检定申请、KP 私有台账。

三样东西都只经 KP 鉴权的接口读写（/kp/desk、/kp/action），不进玩家可见路径：
私有台账里有暗投结果，检定申请里有玩家的意图原文。数据落在 GameSession.kp_state
（与 KP 笔记同一处），玩家侧的会话接口从不序列化这个字段。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.event_log import EventLog
from app.models.module import Module
from app.models.session import GameSession
from app.services import reward_settlement, session_service
from app.services.event_protocol import make_chunk as _make_chunk

LEDGER_LIMIT = 60
# KP 能改写 / 撤回的只有自己手发的这几类：系统结算行（骰子、扣血）改了会与状态对不上
_EDITABLE_TYPES = ("narration", "dialogue", "ooc")


def _state(game_session: GameSession) -> dict:
    return dict(game_session.kp_state or {})


def _save(db: Session, game_session: GameSession, state: dict) -> None:
    game_session.kp_state = state
    db.commit()


# ── 私有台账 ─────────────────────────────────────────────────────────────

def record_ledger(
    db: Session, game_session: GameSession, action: str, result: str, chunks: list,
) -> None:
    """记下一次 KP 动作的结果（含暗投点数），并挂上它发布的旁白 / 台词，供日后改写或撤回。"""
    event_ids = []
    for chunk in chunks:
        wire = chunk.as_wire() if hasattr(chunk, "as_wire") else {}
        # 旁白经 event_to_chunk 出来是 narration_full
        if wire.get("type") in (*_EDITABLE_TYPES, "narration_full") and wire.get("id"):
            event_ids.append(str(wire["id"]))
    state = _state(game_session)
    ledger = list(state.get("ledger") or [])
    ledger.append({
        "id": uuid.uuid4().hex,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "action": action,
        "result": str(result or "")[:2000],
        "event_ids": event_ids,
    })
    state["ledger"] = ledger[-LEDGER_LIMIT:]
    _save(db, game_session, state)


def _ledger_payload(db: Session, session_id: str, state: dict) -> list[dict]:
    out = []
    for entry in reversed(list(state.get("ledger") or [])):
        events = []
        for eid in entry.get("event_ids") or []:
            ev = db.get(EventLog, eid)
            if ev is not None and ev.session_id == session_id:
                events.append({"id": ev.id, "type": ev.event_type, "content": ev.content,
                               "actor_name": ev.actor_name})
            else:
                events.append({"id": eid, "type": "", "content": "", "withdrawn": True})
        out.append({**entry, "events": events})
    return out


def _editable_event(db: Session, session_id: str, event_id: str) -> EventLog:
    ev = db.get(EventLog, event_id) if event_id else None
    if ev is None or ev.session_id != session_id:
        raise ValueError("找不到这条消息（可能已撤回）")
    meta = ev.metadata_ or {}
    mine = (
        ev.event_type in ("narration", "dialogue") and meta.get("kp_manual")
    ) or (ev.event_type == "ooc" and ev.actor_name == "KP" and not ev.actor_id)
    if not mine:
        raise ValueError("只能改写或撤回 KP 自己发布的旁白、NPC 台词与场外话")
    return ev


def edit_event(db: Session, session_id: str, event_id: str, content: str) -> tuple[list, str]:
    content = (content or "").strip()
    if not content:
        raise ValueError("内容不能为空；想整条拿掉请用撤回")
    ev = _editable_event(db, session_id, event_id)
    ev.content = content
    db.commit()
    return [_make_chunk("event_update", content, event_id=ev.id)], "已改写"


def withdraw_event(db: Session, session_id: str, event_id: str) -> tuple[list, str]:
    ev = _editable_event(db, session_id, event_id)
    db.delete(ev)
    db.commit()
    return [_make_chunk("event_delete", event_id=event_id)], "已撤回"


# ── 玩家的检定申请 ──────────────────────────────────────────────────────

def _check_requests(db: Session, session_id: str, state: dict) -> list[dict]:
    """尚未处理的玩家检定申请：/check 落的那条行动事件（暂存的推进后才算数）。"""
    handled = set(state.get("handled_requests") or [])
    out = []
    for ev in session_service.get_session_events(db, session_id):
        meta = ev.metadata_ or {}
        if ev.event_type != "action" or not meta.get("check_request") or meta.get("pending_turn"):
            continue
        if ev.id in handled:
            continue
        out.append({
            "id": ev.id, "actor_id": ev.actor_id, "actor_name": ev.actor_name,
            "skill": str(meta.get("skill") or ""), "intent": str(meta.get("intent") or ""),
        })
    return out[-20:]


def mark_request_handled(db: Session, game_session: GameSession, request_id: str) -> None:
    if not request_id:
        return
    state = _state(game_session)
    handled = list(state.get("handled_requests") or [])
    if request_id not in handled:
        handled.append(request_id)
        state["handled_requests"] = handled[-200:]
        _save(db, game_session, state)


# ── 待投检定 ────────────────────────────────────────────────────────────

def _pending_checks(game_session: GameSession) -> list[dict]:
    pending = (game_session.turn_state or {}).get("pending_checks") or {}
    out = []
    for check in pending.values():
        if not isinstance(check, dict):
            continue
        out.append({
            "id": check.get("id"), "kind": check.get("kind") or "skill",
            "skill": check.get("skill"), "difficulty": check.get("difficulty") or "normal",
            "actor_name": check.get("actor_name") or check.get("char_ref"),
            "char_id": check.get("char_id"), "reason": check.get("reason") or "",
            "group": bool(check.get("check_batch_id") or check.get("san_batch_id")),
        })
    return out


def cancel_pending_check(
    db: Session, session_id: str, game_session: GameSession, module: Module, check_id: str,
) -> tuple[list, str]:
    """KP 撤销一个还没投的检定。群检里撤掉最后一个没投的人，整批即收束并通知 KP。

    检定没发生，被它门控的收益与线索按「没成」处理（与掷失败同一出口）；群检里已投的人
    有人成功，则按成功结算——撤掉的是「没投的那个人」，不是整次检定。
    """
    check = session_service.pop_pending_check(db, session_id, check_id)
    if check is None:
        raise ValueError("这个检定已经投过或已撤销")
    name = str(check.get("actor_name") or check.get("char_ref") or "")
    chunks: list = []
    ev = next(
        (e for e in session_service.get_session_events(db, session_id)
         if (e.metadata_ or {}).get("check_request") and (e.metadata_ or {}).get("id") == check_id),
        None,
    )
    if ev is not None:
        ev.metadata_ = {**(ev.metadata_ or {}), "cancelled": True}
        db.commit()
        chunks.append(_make_chunk("event_patch", metadata={"event_id": ev.id, "patch": {"cancelled": True}}))

    note = f"{name} 的检定已被 KP 撤销"
    if check.get("kind") == "san_check" and check.get("san_batch_id"):
        remaining = session_service.append_pending_batch_result(
            db, session_id, str(check["san_batch_id"]), note,
        )
        results = [*(check.get("san_results") or []), note]
    elif check.get("check_batch_id"):
        remaining = session_service.append_pending_group_check_result(
            db, session_id, str(check["check_batch_id"]), note, succeeded=False, fumbled=False,
        )
        results = [*(check.get("check_results") or []), note]
    else:
        remaining, results = 0, []
    if remaining:
        return chunks, f"{note}（同批还有 {remaining} 人未投）"

    if check.get("kind") != "san_check":
        db.refresh(game_session)
        succeeded = bool(check.get("check_any_success"))
        chunks.extend(reward_settlement.settle_check_rewards(
            db, session_id, game_session, succeeded, module=module,
        ))
    if len(results) > 1:
        desc = "\n".join(str(r) for r in results)
        chunks.append(_make_chunk(
            "kp_roll_ready", "群体检定已收束，等待真人 KP 处理后果", metadata={"description": desc},
        ))
        return chunks, f"{note}；整批已收束：\n{desc}"
    return chunks, note


def desk_payload(db: Session, session_id: str, game_session: GameSession) -> dict:
    state = _state(game_session)
    return {
        "pending_checks": _pending_checks(game_session),
        "check_requests": _check_requests(db, session_id, state),
        "ledger": _ledger_payload(db, session_id, state),
    }
