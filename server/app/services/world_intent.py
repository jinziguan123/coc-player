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

from app.models.session import GameSession

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


def from_npc(
    *, actor_id: str, actor_name: str, target: str = "", verb: str = "",
    content: str = "", observes_seq: int = 0,
) -> WorldIntent:
    return WorldIntent(
        actor_id=actor_id, actor_name=actor_name, source="npc",
        action="act" if verb else "speak", target=target, verb=verb,
        content=content, observes_seq=observes_seq,
    )


def from_combat(actor: dict, action: dict, state: dict) -> WorldIntent:
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
    })
    ws[HISTORY_KEY] = history[-_HISTORY_LIMIT:]
    return ws


def record(db: Session, session_id: str, intent: WorldIntent) -> None:
    """best-effort 落库；失败不阻塞动作本身。"""
    try:
        session = db.get(GameSession, session_id)
        if session is None:
            return
        session.world_state = record_state(session.world_state or {}, intent)
        db.add(session)
        db.commit()
        db.refresh(session)
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
