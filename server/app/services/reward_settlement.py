"""检定收益结算：消费暂存、库存、事件及线索台账的事务边界。

不依赖规划器、回合编排或会话服务；配图由调用方注入，且仅在提交后执行。
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.domain.checks import skill_matches
from app.models.character import Character
from app.models.module import Module
from app.models.session import GameSession
from app.services import auto_check_state, effect_channel, event_store, inventory_service, world_memory
from app.services.event_protocol import make_chunk as _make_chunk

logger = logging.getLogger(__name__)


def apply_item_change(
    db: Session, session_id: str, game_session: GameSession, target: Character,
    change: "effect_channel.ItemChange", anchor: int, source: str = "planner", *, commit: bool = True,
) -> list[str]:
    """物品增减的确定性执行（效果通道）。按台账键幂等；失去时即便没匹配到也记键，避免反复尝试。"""
    def already() -> bool:
        return change.ledger_key in set((game_session.turn_state or {}).get("item_delta_keys") or [])

    def apply(receipt: dict) -> list[str]:
        chunks: list[str] = []
        if change.op == "gain":
            inventory_service.add_item(
                db, target, change.name, qty=change.qty, kind=(change.kind or None), commit=False,
            )
            suffix = f"×{change.qty}" if change.qty > 1 else ""
            content, flag = f"{target.name} 获得了 {change.name}{suffix}", "item_gain"
            changed = True
        else:
            changed = bool(inventory_service.remove_by_name(
                db, target, change.name, qty=change.qty, commit=False,
            ))
            content, flag = f"{target.name} 失去了 {change.name}", "item_loss"
        if changed:
            ev = event_store.add_event(
                db, session_id, "system", content, actor_name="系统", commit=False,
                metadata={flag: True, "char_id": target.id, "effect": receipt},
            )
            chunks.append(_make_chunk("system", ev.content, event_id=ev.id, metadata={flag: True}))
            chunks.append(_make_chunk("inventory_update", metadata={"char_id": target.id}))
        ts = dict(game_session.turn_state or {})
        ts["item_delta_keys"] = [*(ts.get("item_delta_keys") or []), change.ledger_key]
        game_session.turn_state = ts
        return chunks

    return effect_channel.execute(
        db, session_id, effect_channel.EffectProposal(change, source, anchor),
        apply, duplicate=[], already=already, commit=commit,
    )


def discard_pending_item_gains(db: Session, game_session: GameSession, *, commit: bool = True) -> bool:
    """作废暂存的待检定收益（检定失败 / 玩家干脆没投就又行动了）。返回是否真的清掉了东西。"""
    ws = dict(game_session.turn_state or {})
    if not ws.get("pending_item_gains"):
        return False
    ws["pending_item_gains"] = []
    game_session.turn_state = ws
    if commit:
        db.commit()
    return True


def _stage_pending_items(
    db: Session, session_id: str, game_session: GameSession, succeeded: bool,
) -> list[str]:
    """消费暂存、库存、事件与台账仅暂存，由收益入口统一提交。"""
    ws = dict(game_session.turn_state or {})
    pending = list(ws.get("pending_item_gains") or [])
    if not pending:
        return []
    ws["pending_item_gains"] = []
    game_session.turn_state = ws
    if not succeeded:
        return [_make_chunk("inventory_update")]
    chunks: list[str] = []
    for item in pending:
        target = db.get(Character, str(item.get("char_id") or ""))
        name = str(item.get("name") or "").strip()
        if target is None or not name:
            continue
        key = str(item.get("key") or "") or f"g|stash|{name}|{target.id}"
        chunks += apply_item_change(db, session_id, game_session, target, effect_channel.ItemChange(
            target.id, name, int(item.get("qty") or 1), "gain", key, str(item.get("kind") or ""),
        ), 0, source="planner", commit=False)
    return chunks


def _stage_pending_clues(game_session: GameSession, succeeded: bool) -> list[str]:
    """线索候选消费与台账更新仅暂存，返回提交后需要配图的首次发现。"""
    ws = dict(game_session.world_state or {})
    staged = ws.get("pending_clue_reveals") or {}
    ids = list(dict.fromkeys(str(c) for c in (staged.get("ids") or []) if str(c or "").strip()))
    ws.pop("pending_clue_reveals", None)
    before = set(ws.get("clue_ledger") or {})
    if ids and succeeded:
        ws = world_memory.record_clue_reveal(
            ws, ids, "direct", list(staged.get("discovered_by") or []),
            int(staged.get("seq") or 0), note=str(staged.get("note") or ""),
        )
    game_session.world_state = ws
    return [cid for cid in ids if cid not in before] if succeeded else []


def _notify_first_clues(db, session_id, module, ids, on_first_clue):
    """配图是提交后的可选副作用，失败不能让调用方误以为收益未落库。"""
    if module is None or on_first_clue is None:
        return
    for cid in ids:
        try:
            on_first_clue(db, session_id, module, cid)
        except Exception:
            db.rollback()
            logger.exception("线索配图失败（收益已提交）：session=%s clue=%s", session_id, cid)


def settle_pending_item_gains(
    db: Session, session_id: str, game_session: GameSession, succeeded: bool,
) -> list[str]:
    """兼容物品单独结算入口；整个待兑现批次要么成功，要么保留供重试。"""
    with effect_channel.transaction(db):
        chunks = _stage_pending_items(db, session_id, game_session, succeeded)
    return chunks


def settle_pending_clue_reveals(
    db: Session, session_id: str, game_session: GameSession, succeeded: bool,
    module: Module | None = None, on_first_clue=None,
) -> None:
    """兼容线索单独结算入口；首次发现的配图仅在提交后执行。"""
    with effect_channel.transaction(db):
        ids = _stage_pending_clues(game_session, succeeded)
    _notify_first_clues(db, session_id, module, ids, on_first_clue)


def settle_check_rewards(
    db: Session, session_id: str, game_session: GameSession, succeeded: bool, *,
    module: Module | None = None, on_first_clue=None, skill: str | None = None,
    event_id: str | None = None,
) -> list[str]:
    """一次检定的收益同事务；自动检定按技能筛选，并与检定标记一起落库。"""
    chunks, ids = [], []
    with effect_channel.transaction(db):
        if event_id is not None:
            auto_check_state.record(game_session, event_id=event_id, skill=skill or "", succeeded=succeeded)
        gains = list((game_session.turn_state or {}).get("pending_item_gains") or [])
        if skill is None or (gains and all(skill_matches(g.get("skill", ""), skill) for g in gains)):
            chunks = _stage_pending_items(db, session_id, game_session, succeeded)
        staged = (game_session.world_state or {}).get("pending_clue_reveals") or {}
        if skill is None or (staged and skill_matches(staged.get("skill", ""), skill)):
            ids = _stage_pending_clues(game_session, succeeded)
    _notify_first_clues(db, session_id, module, ids, on_first_clue)
    return chunks


def settle_auto_rewards(
    db: Session, session_id: str, game_session: GameSession, *, skill: str, succeeded: bool,
    module=None, on_first_clue=None, event_id: str | None = None,
) -> list:
    """自动检定标记、匹配的物品和线索一次提交。"""
    return settle_check_rewards(
        db, session_id, game_session, succeeded, skill=skill, event_id=event_id,
        module=module, on_first_clue=on_first_clue,
    )
