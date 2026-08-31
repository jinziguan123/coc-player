"""房间状态快照：``sync`` 类事件对应的真值出口。

`room_events` 把事件分成三类，其中 ``sync`` 类只承担「某状态变了」的通知，
真值在业务表里。此前这些真值散落在各自的 GET 端点上，而且并非每个系统都有：

- 战斗、追逐各有 ``GET /sessions/{id}/combat`` 与 ``/chase``；
- 回合确认（``turn_state``）**根本没有查询端点**，只能靠广播事件——断线期间错过
  那条广播，确认进度就一直是错的，直到下一次有人确认；
- 三个系统三次往返，跨网联机时每次重连要多付两个 RTT。

这里把它们收成一个注册表和一次查询。新增一个需要断线对齐的系统时，
在 ``PROVIDERS`` 里加一行即可，不必再改前端的重连流程。

``seq`` 是快照对应的事件水位线：客户端拿到快照后，可以安全地丢弃 seq 不大于它的
缓冲事件，剩下的按序应用即可对齐。
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session

from app.models.session import GameSession
from app.services import chase_service, combat_service, session_service
from app.services.event_protocol import luck_offer_event_id

# 系统名 → 取快照。键即前端 sync 事件的归属系统，两边用同一套名字。
PROVIDERS: dict[str, Callable[[Session, GameSession], dict]] = {}


def register(name: str):
    def wrap(fn: Callable[[Session, GameSession], dict]):
        PROVIDERS[name] = fn
        return fn

    return wrap


@register("combat")
def _combat_snapshot(db: Session, session: GameSession) -> dict:
    state = combat_service.get_combat(session)
    if not state:
        return {"active": False}
    return combat_service._combat_meta(state) | {"active": True}


@register("chase")
def _chase_snapshot(db: Session, session: GameSession) -> dict:
    state = chase_service.get_chase(session)
    if not state:
        return {"active": False}
    return chase_service._meta(state) | {"active": True}


@register("turn")
def _turn_snapshot(db: Session, session: GameSession) -> dict:
    # 在线集合交给调用方注入过——这里按「全体应确认者」取，重连场景下宁可多显示
    # 一个待确认者，也好过让已确认的人看不到自己的确认。
    return session_service.turn_confirm_state(db, session.id)


@register("luck")
def _luck_snapshot(db: Session, session: GameSession) -> dict:
    """待决的幸运询价。

    这一项和上面几个不一样：它不是「HUD 显示得对不对」的问题，而是**流程会不会卡死**。
    询价一旦发出，整条结算链（物品发货、线索记账、KP 续写）就停在那儿等回答，
    而 ``pending_luck`` 是落在 turn_state 里的持久状态。可 ``luck_offer`` 事件是 log 类、
    **不落库**——玩家刷新或断线一次，那张卡就再也回不来了，人却还在等他拍板。
    实测有存档正是这么停在一次侦查检定上，之后一个事件都没有。

    返回的字段与广播时的 metadata 一致，前端两条路复用同一套渲染。
    """
    pending = session_service.get_pending_luck(db, session.id) or {}
    offer = pending.get("offer") or {}
    if not pending or not offer:
        return {"pending": False}
    return {
        "pending": True,
        # 与广播同一个 id：前端按 id 幂等，重连补的与广播来的会合成一条
        "id": luck_offer_event_id(str(pending.get("dice_event_id") or "")),
        "char_id": pending.get("char_id") or "",
        "actor": pending.get("shown_name") or pending.get("disp_name") or "",
        "skill": pending.get("skill") or "",
        "dice_event_id": pending.get("dice_event_id") or "",
        "cost": offer.get("cost"),
        "reroll_cost": offer.get("reroll_cost"),
        "available": offer.get("available"),
        "target": offer.get("target"),
    }


def snapshot(db: Session, session: GameSession, systems: list[str] | None = None) -> dict:
    """取一批系统的快照。``systems`` 为空表示全取；未知系统名忽略而不是报错，
    这样前端可以先请求新系统、后端旧版本也不会 400。"""
    wanted = [s for s in (systems or PROVIDERS.keys()) if s in PROVIDERS]
    return {name: PROVIDERS[name](db, session) for name in wanted}


def current_seq(db: Session, session_id: str) -> int:
    """当前事件水位线。没有任何事件时为 0。

    直接取 max(sequence_num)，不复用 ``get_latest_events``——后者会过滤「仅 KP 可见」
    事件，若最后一条恰好是幕后推演就会返回空、把水位线错报成 0。水位线是传输层的
    序号，与可见性无关。
    """
    from sqlalchemy import func

    from app.models.event_log import EventLog

    value = (
        db.query(func.max(EventLog.sequence_num))
        .filter(EventLog.session_id == session_id)
        .scalar()
    )
    return int(value or 0)
