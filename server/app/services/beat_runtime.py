"""剧情事件效果执行器（v3）。

把模组事件里声明的 effects 落到真实世界状态：搬人、置标志、揭示线索、结算结局。
只做**确定性**部分；检定与 SAN 不在这里掷（避免绕过规则内核与玩家投骰），
由执行器返回一句指令，让 KP 用既有的 dice_check / san_check 工具发起。

调用方：kp_tool_loop 的 beat 工具处理器（[BEAT: 事件id]）。
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models.character import Character
from app.models.module import Module
from app.models.session import GameSession
from app.services import inventory_service, navigation_service, session_service, world_memory

logger = logging.getLogger(__name__)


def _party(player_char: Character, teammates: list[Character] | None) -> list[Character]:
    return [player_char, *(teammates or [])]


def _all_effects(effects: list) -> list[dict]:
    return [e for e in effects or [] if isinstance(e, dict)]


def execute_event(
    db: Session,
    session_id: str,
    game_session: GameSession,
    module: Module,
    event: dict,
    player_char: Character,
    teammates: list[Character] | None = None,
    *,
    seq: int = 0,
) -> str:
    """执行一个事件的确定性效果，返回给 KP 的结果说明（空串表示无需回注）。"""
    notes: list[str] = []
    deferred: list[str] = []
    party = _party(player_char, teammates)
    location_ids = {
        str(s.get("id")) for s in (module.scenes or [])
        if isinstance(s, dict) and s.get("id")
    }
    clue_ids = {
        str(c.get("id")) for c in (module.clues or [])
        if isinstance(c, dict) and c.get("id")
    }

    for effect in _all_effects(event.get("effects")):
        etype = str(effect.get("type") or "")
        try:
            if etype == "set_flag":
                session_service.set_flag(
                    db, session_id, str(effect.get("flag") or ""), True, commit=False,
                )
                notes.append(f"置标志 {effect.get('flag')}")
            elif etype == "clear_flag":
                session_service.set_flag(
                    db, session_id, str(effect.get("flag") or ""), False, commit=False,
                )
                notes.append(f"清标志 {effect.get('flag')}")
            elif etype == "goto":
                target = str(effect.get("target") or "").strip()
                if not target:
                    continue
                if target not in location_ids:
                    notes.append(f"（目标地点 {target} 不存在，未移动）")
                    continue
                movers = _resolve_movers(effect, party)
                for char in movers:
                    navigation_service.set_char_location(
                        db, session_id, char.id, target, commit=False,
                    )
                db.commit()
                db.refresh(game_session)
                names = "、".join(c.name for c in movers) or "队伍"
                notes.append(f"{names}前往 {target}")
            elif etype == "reveal_clue":
                clue_id = str(effect.get("clue_id") or "").strip()
                if clue_id and clue_id not in clue_ids:
                    notes.append(f"（线索 {clue_id} 不存在，未揭示）")
                elif clue_id:
                    ws = dict(game_session.world_state or {})
                    ws = world_memory.record_clue_reveal(
                        ws, [clue_id], "direct",
                        [c.id for c in party], seq, note="事件效果",
                    )
                    game_session.world_state = ws
                    db.commit()
                    notes.append(f"揭示线索 {clue_id}")
            elif etype == "ending":
                ending_id = str(effect.get("ending_id") or "").strip()
                if ending_id:
                    ending = next(
                        (e for e in (module.endings or [])
                         if isinstance(e, dict) and str(e.get("id")) == ending_id),
                        None,
                    )
                    if ending:
                        ws = dict(game_session.world_state or {})
                        ws["ending_reached"] = {
                            "id": ending_id,
                            "name": ending.get("name") or ending_id,
                        }
                        game_session.world_state = ws
                        db.commit()
                        notes.append(f"抵达结局「{ending.get('name') or ending_id}」")
            elif etype in ("grant_item", "remove_item"):
                item = str(effect.get("item") or "").strip()
                if not item:
                    continue
                for char in party:
                    if etype == "grant_item":
                        inventory_service.add_item(db, char, item, qty=int(effect.get("qty") or 1))
                    else:
                        inventory_service.remove_by_name(db, char, item)
                notes.append(("获得 " if etype == "grant_item" else "失去 ") + item)
            elif etype in ("damage", "heal"):
                amount = _roll_amount(str(effect.get("dice") or effect.get("amount") or "0"))
                if amount:
                    targets = _resolve_targets(effect, party)
                    for char in targets:
                        _change_hp(db, char, -amount if etype == "damage" else amount)
                    notes.append(
                        f"{'HP-' if etype == 'damage' else 'HP+'} {amount}"
                        f"（{'、'.join(c.name for c in targets)}）"
                    )
            elif etype == "npc_state":
                _apply_npc_state(game_session, db, effect)
                notes.append(f"NPC {effect.get('npc_id')} 状态更新")
            elif etype == "unlock":
                _set_path_block(db, session_id, str(effect.get("location_id") or ""), blocked=False)
                notes.append(f"解锁 {effect.get('location_id')}")
            elif etype == "block":
                _set_path_block(db, session_id, str(effect.get("location_id") or ""), blocked=True)
                notes.append(f"封路 {effect.get('location_id')}")
            elif etype == "narration":
                text = str(effect.get("text") or "").strip()
                if text:
                    notes.append(text)
            elif etype == "check":
                branch = "成功/失败分支"
                deferred.append(
                    f"「{event.get('title') or event.get('id')}」需要 {effect.get('skill') or '技能'}"
                    f"检定（{branch}由你在检定结果出来后按事件说明叙述）"
                )
            elif etype == "sanity":
                deferred.append(
                    f"「{event.get('title') or event.get('id')}」需要 SAN 检定 {effect.get('loss')}"
                    "（用 san_check 工具发起）"
                )
        except Exception:
            logger.exception(
                "事件效果执行失败: event=%s type=%s session=%s",
                event.get("id"), etype, session_id,
            )
            notes.append(f"（{etype} 执行失败，已完成其余效果）")

    _mark_seen(db, game_session, event)

    parts = []
    if notes:
        parts.append("系统已结算：" + "；".join(notes) + "。叙述必须与这些结果一致。")
    if deferred:
        parts.append("；".join(deferred) + "。")
    return "\n".join(parts)


def _resolve_movers(effect: dict, party: list[Character]) -> list[Character]:
    who = str(effect.get("who") or "party").strip().lower()
    if who in ("actor", "self"):
        return party[:1]
    return party


def _resolve_targets(effect: dict, party: list[Character]) -> list[Character]:
    who = str(effect.get("targets") or effect.get("who") or "party").strip().lower()
    if who in ("actor", "self"):
        return party[:1]
    return party


def _change_hp(db: Session, char: Character, delta: int) -> None:
    sd = dict(char.system_data or {})
    hp = dict(sd.get("hitPoints") or {})
    current = int(hp.get("current") or 0)
    maximum = int(hp.get("max") or current)
    new_value = max(0, min(maximum if maximum else 999, current + delta))
    hp["current"] = new_value
    sd["hitPoints"] = hp
    char.system_data = sd
    db.add(char)


def _roll_amount(expr: str) -> int:
    """只认纯数字与极简 NdM；其余返回 0（交给 KP 用规则工具结算）。"""
    expr = (expr or "").strip()
    if not expr:
        return 0
    try:
        if expr.isdigit():
            return int(expr)
        if "d" in expr.lower():
            import random

            count, sides = expr.lower().split("d", 1)
            n = int(count or "1")
            m = int(sides)
            if 1 <= n <= 20 and 2 <= m <= 100:
                return sum(random.randint(1, m) for _ in range(n))
    except Exception:
        return 0
    return 0


def _apply_npc_state(game_session: GameSession, db: Session, effect: dict) -> None:
    ws = dict(game_session.world_state or {})
    runtime = dict(ws.get("npc_runtime") or {})
    nid = str(effect.get("npc_id") or "")
    if not nid:
        return
    state = dict(runtime.get(nid) or {})
    for key in ("alive", "location", "attitude"):
        if key in effect:
            state[key] = effect[key]
    runtime[nid] = state
    ws["npc_runtime"] = runtime
    game_session.world_state = ws
    db.commit()


def _set_path_block(db: Session, session_id: str, scene_id: str, *, blocked: bool) -> None:
    if not scene_id:
        return
    session = db.get(GameSession, session_id)
    if session is None:
        return
    ws = dict(session.world_state or {})
    blocked_ids = {str(x) for x in (ws.get("blocked_paths") or [])}
    if blocked:
        blocked_ids.add(scene_id)
    else:
        blocked_ids.discard(scene_id)
    ws["blocked_paths"] = sorted(blocked_ids)
    session.world_state = ws
    db.commit()


def _mark_seen(db: Session, game_session: GameSession, event: dict) -> None:
    eid = str(event.get("id") or "")
    if not eid:
        return
    ws = dict(game_session.world_state or {})
    seen = list(ws.get("flow_seen") or [])
    if eid not in seen:
        seen.append(eid)
    ws["flow_seen"] = seen
    game_session.world_state = ws
    db.commit()
