"""追逐状态机（P5）：把纯引擎 chase.py 接到会话 world_state.chase 上。

抽象距离轨：玩家(逃方 quarry) vs 追方(pursuer)，每次玩家「奔逃/闯障」推进一轮，
引擎按 MOV 调整的对抗推动 gap，越阈值判脱身/被追上，结果折回主 KP（chase_result）。
子代理叙述复用 CombatAgent（有 agent 时）。
"""

from __future__ import annotations

import copy

from sqlalchemy.orm import Session

from app.models.character import Character
from app.models.chase_state import ChaseState
from app.models.session import GameSession
from app.rules.coc import chase as engine
from app.ai.provider import model_meta
from app.services import session_service
from app.services.event_protocol import make_chunk
from app.services.room_events import RoomEvent


def _chunk(t: str, content: str = "", **extra) -> RoomEvent:
    """本模块的事件构造快捷方式（``id=`` 是 ``event_id`` 的历史写法）。见 combat_service 同名函数。"""
    return make_chunk(
        t, content,
        actor_name=extra.get("actor_name"),
        metadata=extra.get("metadata"),
        event_id=extra.get("id"),
        actor_id=extra.get("actor_id"),
    )


def get_chase(session: GameSession) -> dict | None:
    # 追逐态已拆到独立表（chase_states）。深拷贝：调用方（resolve_chase_round）会在返回的
    # state 上原地改 gap/round，不能碰到 ORM 挂着的 JSON 值（与 get_combat 同一口径）。
    cs = session.chase_state
    c = cs.state if cs is not None else None
    return copy.deepcopy(c) if c and c.get("active") else None


def _save(db: Session, session_id: str, state: dict | None) -> None:
    # 唯一写入口：state=None → 删行（结束追逐）；否则 upsert 该会话的追逐态行。
    cs = db.get(ChaseState, session_id)   # session_id 即主键，1:1
    if state is None:
        if cs is not None:
            db.delete(cs)
    elif cs is None:
        db.add(ChaseState(session_id=session_id, state=state))
    else:
        cs.state = state
    db.commit()


def _char_data(p: dict) -> dict:
    return {"skills": p.get("skills") or {}, "base_attributes": p.get("base_attributes") or {},
            "system_data": p.get("system_data") or {}}


def _quarry_from_char(char: Character) -> dict:
    sd = char.system_data or {}
    return {"name": char.name, "char_id": char.id, "mov": (sd.get("move") or 8),
            "skills": char.skills or {}, "base_attributes": char.base_attributes or {},
            "system_data": sd}


def _pursuer_from_npc(npc: dict) -> dict:
    attrs = npc.get("attributes") or {}
    from app.rules.coc.character import compute_derived
    mov = 8
    try:
        mov = compute_derived(attrs).get("move", 8)
    except Exception:
        pass
    return {"name": npc.get("name") or "追兵", "mov": npc.get("mov") or mov,
            "skills": npc.get("skills") or {}, "base_attributes": attrs, "system_data": {}}


def start_chase(db: Session, session_id: str, quarry: dict, pursuer: dict,
                *, skill: str = "运动", escape_at: int = 5, caught_at: int = -3,
                trigger: str = "") -> tuple[dict, list[str]]:
    """建立追逐态：gap 从 0 起，玩家逃、pursuer 追。返回 (state, chunks)。"""
    state = {
        "active": True, "round": 0, "gap": 0, "skill": skill,
        "escape_at": escape_at, "caught_at": caught_at,
        "quarry": quarry, "pursuer": pursuer, "trigger": trigger,
    }
    _save(db, session_id, state)
    return state, [_chunk("chase_start", trigger or "追逐开始！", metadata=_meta(state))]


def _meta(state: dict) -> dict:
    return {"round": state["round"], "gap": state["gap"],
            "escape_at": state["escape_at"], "caught_at": state["caught_at"],
            "quarry": state["quarry"]["name"], "pursuer": state["pursuer"]["name"]}


async def resolve_chase_round(db: Session, session_id: str, action: dict,
                              agent=None, scene_hint: str = "") -> list[str]:
    """玩家推进一轮追逐（action 可含 hazard={who,skill,difficulty}）。更新 gap、判脱身/被追上。"""
    session = db.get(GameSession, session_id)
    state = get_chase(session)
    if not state:
        raise ValueError("当前不在追逐中")
    q, p = state["quarry"], state["pursuer"]
    res = engine.resolve_chase_round(
        _char_data(q), _char_data(p), skill=state["skill"],
        quarry_mov=q.get("mov", 8), pursuer_mov=p.get("mov", 8),
        hazard=action.get("hazard"),
    )
    state["round"] += 1
    state["gap"] += res["gap_delta"]

    chunks: list[str] = []
    line = (f"{q['name']} {res['quarry_check'].description} / "
            f"{p['name']} {res['pursuer_check'].description} → 距离 {'+' if res['gap_delta']>=0 else ''}{res['gap_delta']}"
            f"（当前 {state['gap']}）")
    ev = session_service.add_event(db, session_id, "dice", line, actor_name="追逐")
    chunks.append(_chunk("dice", line, id=ev.id))

    outcome = engine.check_chase_end(state["gap"], state["escape_at"], state["caught_at"])
    if outcome:
        chunks += _end_chase(db, session_id, state, outcome)
    else:
        _save(db, session_id, state)
        chunks.append(_chunk("chase_state", metadata=_meta(state)))
        if agent:
            prose = await agent.narrate(
                {"round": state["round"], "initiative": []}, [line], scene_hint)
            if prose:
                pev = session_service.add_event(
                    db, session_id, "narration", prose, actor_name="KP",
                    metadata=model_meta(agent),
                )
                chunks.insert(0, _chunk("narration_full", prose, id=pev.id, actor_name="KP"))
    return chunks


def _end_chase(db: Session, session_id: str, state: dict, outcome: str) -> list[str]:
    """结束追逐：产出 chase_result 摘要（复用 KP 折回：_format_combat_result 已识别 escaped/caught），清态。"""
    summary = {"outcome": outcome, "rounds": state["round"],
               "casualties": [], "hp_after": {}}
    session = db.get(GameSession, session_id)
    cs = db.get(ChaseState, session_id)
    if cs is not None:
        db.delete(cs)   # 追逐态已拆表：结束 = 删该会话的 chase_states 行，不再 world_state.pop("chase")
    ws = dict(session.world_state or {})
    ws["combat_result"] = summary   # 复用同一折回通道
    session.world_state = ws
    db.commit()
    label = {"escaped": "追逐结束：成功甩脱追兵。", "caught": "追逐结束：被追上了！"}.get(outcome, "追逐结束。")
    ev = session_service.add_event(db, session_id, "system", label, actor_name="追逐")
    return [_chunk("system", label, id=ev.id), _chunk("chase_end", label, metadata=summary)]
