"""回合状态服务：``GameSession.turn_state`` 的待投检定与回合确认。

自 ``session_service.py`` 拆出。回合锁（确认制）与 pending_checks 同属一个
职责簇——它们都围绕「本回合尚未定稿」的短生命周期状态。``session_service``
保留同名 re-export。
"""

from __future__ import annotations

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.models.session import GameSession
from app.models.session_participant import SessionParticipant

from app.services.event_store import get_session_events

#: turn_state 顶层里不属于「回合确认」的常驻键。旧迁移曾把确认项直接铺在顶层，
#: 读取时据此把它们与 pending_checks 等区分开。
_EPHEMERAL_TURN_STATE_KEYS = frozenset({
    "pending_checks", "pending_item_gains", "item_delta_keys", "pending_luck",
})


def _human_participants(db: Session, session_id: str) -> list[SessionParticipant]:
    """本会话所有已填角色的真人席位（回合确认需要逐个确认推进的主体）。"""
    return (
        db.query(SessionParticipant)
        .filter(
            SessionParticipant.session_id == session_id,
            SessionParticipant.role == "human",
            SessionParticipant.character_id.is_not(None),
        )
        .all()
    )


def _confirm_map(turn_state: dict | None) -> dict:
    """读取回合确认表：优先新口径 ``turn_state.turn_confirm``；兼容旧迁移铺在顶层的数据。"""
    ts = turn_state or {}
    nested = ts.get("turn_confirm")
    if isinstance(nested, dict):
        return dict(nested)
    return {
        str(k): True
        for k, v in ts.items()
        if k not in _EPHEMERAL_TURN_STATE_KEYS and v is True
    }


def _write_confirm_map(ts: dict, confirm: dict) -> dict:
    """写回合确认表并把旧迁移铺在顶层的确认键清掉（保留 pending 等常驻键）。"""
    ts = dict(ts)
    ts["turn_confirm"] = confirm
    for key in list(ts):
        if key != "turn_confirm" and key not in _EPHEMERAL_TURN_STATE_KEYS:
            ts.pop(key, None)
    return ts


def set_pending_luck(db: Session, session_id: str, offer: dict | None) -> None:
    """挂起 / 清除「等玩家决定花不花幸运」。

    同一时刻至多一份：这个断点会把整条结算链停住，允许并存两份就等于允许两条链各自往下跑。
    """
    session = db.get(GameSession, session_id)
    if not session:
        return
    ts = dict(session.turn_state or {})
    if offer is None:
        ts.pop("pending_luck", None)
    else:
        ts["pending_luck"] = offer
    session.turn_state = ts
    db.commit()


def get_pending_luck(db: Session, session_id: str) -> dict | None:
    """读取待决的幸运消费；没有则 None。"""
    session = db.get(GameSession, session_id)
    if not session:
        return None
    pending = (session.turn_state or {}).get("pending_luck")
    return dict(pending) if isinstance(pending, dict) else None


def add_pending_check(db: Session, session_id: str, check: dict) -> None:
    """登记一个「待玩家投骰」的检定（turn_state.pending_checks，按 check_id 存）。"""
    session = db.get(GameSession, session_id)
    if not session:
        return
    ws = dict(session.turn_state or {})
    pending = dict(ws.get("pending_checks") or {})
    pending[check["id"]] = check
    ws["pending_checks"] = pending
    session.turn_state = ws
    db.commit()


def get_pending_check(
    db: Session,
    session_id: str,
    check_id: str,
) -> dict | None:
    """按 id 读取待投检定，不移除状态。"""
    session = db.get(GameSession, session_id)
    if not session:
        return None
    pending = (session.turn_state or {}).get("pending_checks") or {}
    check = pending.get(check_id)
    return dict(check) if isinstance(check, dict) else None


def find_pending_check(
    db: Session, session_id: str, char_id: str | None, skill: str, difficulty: str,
) -> dict | None:
    """查是否已存在等价的待投检定（同 角色+技能+难度）。用于去重——分头行动下同一 plan 注入
    每个分组，多组会各自吐出同一条 [DICE_CHECK]，合并处理会重复挂 pending / 弹重复投骰卡。"""
    session = db.get(GameSession, session_id)
    if not session:
        return None
    pending = (session.turn_state or {}).get("pending_checks") or {}
    for c in pending.values():
        if (
            c.get("char_id") == char_id
            and c.get("skill") == skill
            and (c.get("difficulty") or "normal") == (difficulty or "normal")
        ):
            return c
    return None


def find_pending_san_check(
    db: Session, session_id: str, char_id: str, source: str,
) -> dict | None:
    """查找同一角色、同一恐怖源尚未完成的 SAN 检定。"""
    session = db.get(GameSession, session_id)
    if not session:
        return None
    pending = (session.turn_state or {}).get("pending_checks") or {}
    for check in pending.values():
        if (
            isinstance(check, dict)
            and check.get("kind") == "san_check"
            and check.get("char_id") == char_id
            and (check.get("source") or "") == (source or "")
        ):
            return dict(check)
    return None


def append_pending_batch_result(
    db: Session, session_id: str, batch_id: str, description: str,
) -> int:
    """把已完成结果追加到同批剩余待投项，返回仍待投的人数。"""
    session = db.get(GameSession, session_id)
    if not session:
        return 0
    ws = dict(session.turn_state or {})
    pending = dict(ws.get("pending_checks") or {})
    remaining = 0
    for check_id, raw in list(pending.items()):
        if not isinstance(raw, dict) or raw.get("san_batch_id") != batch_id:
            continue
        check = dict(raw)
        results = list(check.get("san_results") or [])
        results.append(description)
        check["san_results"] = results
        pending[check_id] = check
        remaining += 1
    if remaining:
        ws["pending_checks"] = pending
        session.turn_state = ws
        db.add(session)
        db.commit()
    return remaining


def append_pending_group_check_result(
    db: Session,
    session_id: str,
    batch_id: str,
    description: str,
    *,
    succeeded: bool,
    fumbled: bool,
) -> int:
    """把一名真人的公开群检结果追加到同批剩余待投项，并合并批次结果标志。"""
    session = db.get(GameSession, session_id)
    if not session:
        return 0
    ws = dict(session.turn_state or {})
    pending = dict(ws.get("pending_checks") or {})
    remaining = 0
    for check_id, raw in list(pending.items()):
        if not isinstance(raw, dict) or raw.get("check_batch_id") != batch_id:
            continue
        check = dict(raw)
        results = list(check.get("check_results") or [])
        results.append(description)
        check["check_results"] = results
        check["check_any_success"] = bool(check.get("check_any_success")) or succeeded
        check["check_any_fumble"] = bool(check.get("check_any_fumble")) or fumbled
        pending[check_id] = check
        remaining += 1
    if remaining:
        ws["pending_checks"] = pending
        session.turn_state = ws
        db.add(session)
        db.commit()
    return remaining


def pop_pending_check(db: Session, session_id: str, check_id: str) -> dict | None:
    """取出并移除一个待定检定；不存在返回 None。"""
    session = db.get(GameSession, session_id)
    if not session:
        return None
    ws = dict(session.turn_state or {})
    pending = dict(ws.get("pending_checks") or {})
    check = pending.pop(check_id, None)
    if check is None:
        return None
    ws["pending_checks"] = pending
    session.turn_state = ws
    db.commit()
    return check


def rollback_last_kp_output(db: Session, session_id: str) -> int:
    """回滚「最新一次 KP 会话」的叙事产物，供玩家「重新生成」用。

    删除范围 = 最后一条『玩家方（真人玩家 + AI 队友）行动/发言』之后的：
      - KP 旁白（narration）
      - NPC 台词（dialogue 且行动者不属于玩家方）
      - 待玩家投骰的检定请求（system + metadata.check_request），并清掉对应 pending_checks
    刻意**保留**：玩家/队友的行动与发言、已投出的骰子结果（dice，不重掷）、HP/场景等其他 system。

    这样「重新生成」= 拿本轮玩家与队友的既有输入、以及已定的骰子，重新生成 KP 叙事，
    而不会重跑队友回合、也不会重掷已定的检定。返回删除的事件条数。
    """
    session = db.get(GameSession, session_id)
    if not session:
        return 0
    party_ids = {
        p.character_id
        for p in db.query(SessionParticipant)
        .filter(SessionParticipant.session_id == session_id)
        .all()
    }
    if session.player_character_id:
        party_ids.add(session.player_character_id)

    events = get_session_events(db, session_id, limit=0)
    last_input = -1
    for i, ev in enumerate(events):
        if ev.event_type in ("action", "dialogue") and ev.actor_id in party_ids:
            last_input = i

    removed = 0
    removed_check_ids: list[str] = []
    for ev in events[last_input + 1:]:
        meta = ev.metadata_ or {}
        is_narration = ev.event_type == "narration"
        is_npc_dialogue = ev.event_type == "dialogue" and ev.actor_id not in party_ids
        is_check_request = ev.event_type == "system" and meta.get("check_request")
        if not (is_narration or is_npc_dialogue or is_check_request):
            continue
        if is_check_request and meta.get("id"):
            removed_check_ids.append(meta["id"])
        db.delete(ev)
        removed += 1

    if removed_check_ids:
        ts = dict(session.turn_state or {})
        pending = dict(ts.get("pending_checks") or {})
        for cid in removed_check_ids:
            pending.pop(cid, None)
        ts["pending_checks"] = pending
        session.turn_state = ts

    if removed:
        db.commit()
    return removed
def human_character_ids(db: Session, session_id: str) -> set[str]:
    """本会话所有真人席位的角色 id（回合确认制里需要逐个确认推进的主体）。"""
    return {p.character_id for p in _human_participants(db, session_id)}


def set_turn_confirm(db: Session, session_id: str, char_id: str, confirmed: bool) -> None:
    """记录/撤销某真人角色对『本回合推进』的确认（存 ``turn_state.turn_confirm``）。"""
    session = db.get(GameSession, session_id)
    if not session or not char_id:
        return
    ts = dict(session.turn_state or {})
    tc = _confirm_map(ts)
    if confirmed:
        tc[char_id] = True
    else:
        tc.pop(char_id, None)
    session.turn_state = _write_confirm_map(ts, tc)
    db.commit()


def turn_confirm_state(
    db: Session, session_id: str, online_tokens: set[str] | None = None
) -> dict:
    """当前回合确认进度：{confirmed_ids, total, ready}。ready＝所有「需确认」真人都已确认。

    掉线豁免：给定 online_tokens 时，有归属但不在线的真人自动豁免——否则任一玩家关掉
    浏览器就会让整局永久卡死。无归属席位（纯本机会话）一律计入（无法判在线，按在场处理）。
    不给 online_tokens（旧调用/测试）时退化为「所有真人都需确认」的原行为。
    """
    session = db.get(GameSession, session_id)
    humans = _human_participants(db, session_id)
    if online_tokens is not None:
        humans = [
            p for p in humans
            if (not p.owner_token) or (p.owner_token in online_tokens)
        ]
    required_ids = {p.character_id for p in humans}
    tc = _confirm_map(session.turn_state if session else None)
    confirmed = sorted(cid for cid in required_ids if tc.get(cid))
    total = len(required_ids)
    return {
        "confirmed_ids": confirmed,
        "total": total,
        "ready": total > 0 and len(confirmed) >= total,
    }


def commit_turn(db: Session, session_id: str) -> None:
    """推进：把本回合所有『暂存发言』(metadata.pending_turn) 转正（去标记），并清空确认状态。"""
    session = db.get(GameSession, session_id)
    if not session:
        return
    for ev in get_session_events(db, session_id, limit=0):
        meta = ev.metadata_ or {}
        if meta.get("pending_turn"):
            m = dict(meta)
            m.pop("pending_turn", None)
            ev.metadata_ = m
            flag_modified(ev, "metadata_")
    # 只清回合确认，不清 turn_state 里的其它键（pending_checks / pending_item_gains /
    # item_delta_keys 有各自的消费/作废时机，不能随推进一起抹掉）。
    ts = dict(session.turn_state or {})
    session.turn_state = _write_confirm_map(ts, {})
    db.commit()
