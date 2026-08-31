"""局域网接入名册：谁能连房主的后端，以及此刻谁还在线。

与内置直连那套（``src-tauri/src/netlink/roster.rs``）分工不同，两边都需要：

- 直连的名册在 **传输层**：隧道要不要建，握手时就得定，那会儿请求还没到后端。
  它能把连接**卡住**等房主点头，因为那是一条长连接。
- 这里的名册在 **应用层**：局域网请求直接打到 FastAPI，Tauri 外壳根本不经手。
  HTTP 卡不住——占着连接等人点头只会超时，所以改成「先拒，再来就通了」：陌生客户端
  第一次请求即登记到门口并吃 403，房主批准后它的下一个请求就过。

判定结果有缓存。每个请求都读一次库太重，而名册变动只发生在房主点按钮的那一刻——
批准/拒绝时主动清缓存即可，不必让每个请求都去查。
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy.orm import Session

from app.models.lan_peer import LanPeer

Status = Literal["pending", "approved", "rejected"]

#: 判定缓存活多久。短到房主点完批准客人几乎立刻能进，长到挡住绝大多数重复查库。
_CACHE_TTL = 3.0

#: last_seen 多久才回写一次。在线判定精度到分钟就够，不值得每个请求写一次库。
_TOUCH_INTERVAL = 20.0

#: 多久没露面算离线。SSE 会持续拉取，正常在玩的人远不会碰到这个上限。
ONLINE_WINDOW = timedelta(seconds=90)

# token -> (status, 判定时刻)
_verdicts: dict[str, tuple[Status, float]] = {}
# token -> 上次回写 last_seen 的时刻
_touched: dict[str, float] = {}


def reset_cache() -> None:
    """丢弃进程内缓存。名册一变就要调它，测试之间也要调。"""
    _verdicts.clear()
    _touched.clear()


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def check_in(db: Session, token: str, addr: str | None) -> Status:
    """记一次露面并返回该客户端当前的准入状态。陌生 token 登记为 pending。

    没带 token 的请求一律 pending：客户端不报身份就没法被批准，也不该被放行。
    """
    if not token:
        return "pending"

    now_mono = time.monotonic()
    cached = _verdicts.get(token)
    if cached and now_mono - cached[1] < _CACHE_TTL:
        _maybe_touch(db, token, addr, now_mono)
        return cached[0]

    peer = db.get(LanPeer, token)
    if peer is None:
        peer = LanPeer(token=token, status="pending", last_addr=addr or "")
        db.add(peer)
        db.commit()
    else:
        _write_seen(db, peer, addr)

    _verdicts[token] = (peer.status, now_mono)   # type: ignore[assignment]
    _touched[token] = now_mono
    return peer.status                            # type: ignore[return-value]


def _maybe_touch(db: Session, token: str, addr: str | None, now_mono: float) -> None:
    last = _touched.get(token, 0.0)
    if now_mono - last < _TOUCH_INTERVAL:
        return
    peer = db.get(LanPeer, token)
    if peer is not None:
        _write_seen(db, peer, addr)
    _touched[token] = now_mono


def _write_seen(db: Session, peer: LanPeer, addr: str | None) -> None:
    peer.last_seen = _now()
    if addr:
        peer.last_addr = addr
    db.commit()


def claim_label(db: Session, token: str, label: str) -> None:
    """客人自报名字，只为让房主认得出门口站着谁。存下来但标记为「自称」。"""
    peer = db.get(LanPeer, token)
    if peer is None:
        return
    peer.claimed_label = label.strip()[:40]
    db.commit()


def decide(db: Session, token: str, *, approved: bool, label: str | None = None) -> LanPeer:
    """房主表态。批准时的备注名取用顺序与直连那边一致：房主填的 → 对方自称的 → 空。

    房主填的优先，因为自称不可信；但多数时候房主懒得填，采用自称已经比一串 token 好认。
    """
    peer = db.get(LanPeer, token)
    if peer is None:
        raise ValueError("没有这个客户端")
    peer.status = "approved" if approved else "rejected"
    if approved:
        peer.label = (label or "").strip()[:40] or peer.label or peer.claimed_label
    db.commit()
    db.refresh(peer)
    reset_cache()
    if not approved:
        _cut_live(token)
    return peer


def forget(db: Session, token: str) -> None:
    """把一条记录彻底删掉。对方下次再来会重新排到门口——「重新认识一遍」。"""
    peer = db.get(LanPeer, token)
    if peer is not None:
        db.delete(peer)
        db.commit()
    reset_cache()
    _cut_live(token)


def _cut_live(token: str) -> None:
    """把这个客户端已经建好的实时连接掐掉。

    光改名册不够：403 只挡得住**下一个** HTTP 请求，而 /live 是条已经建立的 SSE，
    不发新请求也照收房间事件。不掐它，「拒绝」和「吊销」就只是名义动作。

    延迟导入避开循环依赖——room_hub 属于传输层，名册不该在模块加载期就把它拖进来。
    """
    from app.services.room_hub import room_hub

    room_hub.disconnect_token(token)


def listing(db: Session) -> list[LanPeer]:
    """名册全量，门口的排在前面，其余按最近露面排序。"""
    peers = db.query(LanPeer).all()
    order = {"pending": 0, "approved": 1, "rejected": 2}
    return sorted(
        peers,
        key=lambda p: (order.get(p.status, 9), -(p.last_seen or _now()).timestamp()),
    )


def is_online(peer: LanPeer) -> bool:
    if peer.last_seen is None:
        return False
    return _now() - peer.last_seen <= ONLINE_WINDOW
