"""NPC / 战斗动作的统一意图记录。

队友走 TeamIntent + resolver；NPC 和战斗动作至少要和它共享同一套「谁、对什么、
做了什么动词、看到的是哪个版本」。这里不做裁决（NPC/战斗已有自己的确定流程），
只负责把动作标准化并写入 world_state.intent_history，供 trace、回放和后续统一
资源图迁移。
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.intent_history import IntentHistory
from app.models.session import GameSession
from app.services import intent_ledger

HISTORY_KEY = "intent_history"
_HISTORY_LIMIT = 100


@dataclass(frozen=True)
class WorldIntent:
    actor_id: str
    actor_name: str
    source: str          # npc / combat
    action: str          # act/attack/flee/dodge...
    target: str
    verb: str
    content: str
    observes_seq: int
    target_version: int = 0
    precondition_ok: bool = True


def from_npc(
    *, actor_id: str, actor_name: str, target: str = "", verb: str = "",
    content: str = "", observes_seq: int = 0,
) -> WorldIntent:
    return WorldIntent(
        actor_id=actor_id, actor_name=actor_name, source="npc",
        action="act" if verb else "speak", target=target, verb=verb,
        content=content, observes_seq=observes_seq,
    )


def _combat_target_alive(state: dict, target_id: str) -> bool:
    if not target_id:
        return True
    for p in state.get("initiative") or []:
        if str(p.get("id") or "") != target_id:
            continue
        return p.get("status") not in ("dead", "unconscious") and int(p.get("hp") or 0) > 0
    return False


def from_combat(
    actor: dict, action: dict, state: dict, world_state: dict | None = None,
) -> WorldIntent:
    target_id = str(action.get("target_id") or "").strip()
    verb = str(action.get("action") or action.get("type") or "").strip()
    if target_id.startswith("npc_"):
        target_key = f"npc:{target_id}"
    elif target_id:
        target_key = f"char:{target_id}"
    else:
        target_key = ""
    return WorldIntent(
        actor_id=str(actor.get("id") or ""),
        actor_name=str(actor.get("name") or ""),
        source="combat",
        action=verb or "act",
        target=target_key,
        verb=verb,
        content=str(action.get("weapon") or ""),
        observes_seq=int(state.get("round") or 0),
        target_version=intent_ledger.version(world_state or {}, target_key),
        precondition_ok=_combat_target_alive(state, target_id),
    )


def record_state(world_state: dict, intent: WorldIntent) -> dict:
    ws = deepcopy(dict(world_state or {}))
    history = list(ws.get(HISTORY_KEY) or [])
    history.append({
        "actor_id": intent.actor_id,
        "actor_name": intent.actor_name,
        "source": intent.source,
        "action": intent.action,
        "target": intent.target,
        "verb": intent.verb,
        "content": intent.content,
        "observes_seq": intent.observes_seq,
        "target_version": intent.target_version,
        "precondition_ok": intent.precondition_ok,
    })
    ws[HISTORY_KEY] = history[-_HISTORY_LIMIT:]
    return ws


def record(db: Session, session_id: str, intent: WorldIntent) -> None:
    """best-effort 落库；失败不阻塞动作本身。"""
    try:
        if db.get(GameSession, session_id) is None:
            return
        db.add(IntentHistory(
            session_id=session_id,
            actor_id=intent.actor_id,
            actor_name=intent.actor_name,
            source=intent.source,
            action=intent.action,
            target=intent.target,
            verb=intent.verb,
            content=intent.content,
            observes_seq=intent.observes_seq,
            target_version=intent.target_version,
            precondition_ok=intent.precondition_ok,
        ))
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass


def list_for_session(
    db: Session, session_id: str, limit: int = 100,
    source: str = "", actor: str = "",
) -> list[dict]:
    """KP 面板用：按来源/角色筛选的最近意图。"""
    q = db.query(IntentHistory).filter(IntentHistory.session_id == session_id)
    if source:
        q = q.filter(IntentHistory.source == source)
    if actor:
        needle = f"%{actor.strip()}%"
        q = q.filter(
            IntentHistory.actor_name.ilike(needle) | IntentHistory.actor_id.ilike(needle)
        )
    rows = q.order_by(IntentHistory.created_at.desc()).limit(
        max(1, min(int(limit), 200))
    ).all()
    return [
        {
            "actor_id": row.actor_id, "actor_name": row.actor_name,
            "source": row.source, "action": row.action, "target": row.target,
            "verb": row.verb, "content": row.content,
            "observes_seq": row.observes_seq,
            "target_version": row.target_version,
            "precondition_ok": row.precondition_ok,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]


def list_history(world_state: dict | None, limit: int = 100) -> list[dict]:
    items = list((world_state or {}).get(HISTORY_KEY) or [])
    return list(reversed(items[-max(1, min(int(limit), 200)):]))
