"""联机可达性设置：查询/切换「允许局域网加入」，以及局域网接入名册。"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import lan_roster, net_access

router = APIRouter(prefix="/api/net", tags=["net"])


def _require_host(request: Request) -> None:
    """名册是房主的事。判定与开关同源——直连客人的源 IP 也是回环，不算本机。"""
    client = request.client.host if request.client else None
    if net_access.peer_kind(client, request.headers) != "local":
        raise HTTPException(403, "只有房主本机可以管理联机名册")


class NetStatus(BaseModel):
    lan_enabled: bool
    """房主是否已允许局域网加入（设置值）。"""
    listening_on_lan: bool
    """当前进程是否真的绑在局域网地址上。与 ``lan_enabled`` 不一致即代表需要重启。"""
    restart_required: bool
    addresses: list[str]
    """本机可供其他玩家填写的地址（不含回环）。"""
    port: int | None = None
    """后端实际监听的端口。桌面版端口是启动时挑的，客人必须连地址+端口才连得上，
    所以要一并给出；开发态直接跑 uvicorn 时未知，由前端回落到当前页面端口。"""


class LanToggle(BaseModel):
    enabled: bool


def _status(request: Request) -> NetStatus:
    enabled = net_access.lan_enabled()
    # 启动时的绑定地址由 run_desktop / uvicorn 决定，进程内记在 app.state 上；
    # 开发态直接跑 uvicorn 时没有这个标记，按「设置值即现状」处理。
    listening = getattr(request.app.state, "listening_on_lan", enabled)
    return NetStatus(
        lan_enabled=enabled,
        listening_on_lan=listening,
        restart_required=enabled != listening,
        addresses=net_access.local_addresses() if enabled else [],
        port=getattr(request.app.state, "bound_port", None),
    )


@router.get("", response_model=NetStatus)
def get_net_status(request: Request) -> NetStatus:
    return _status(request)


@router.post("/lan", response_model=NetStatus)
def set_lan(data: LanToggle, request: Request) -> NetStatus:
    """开关局域网加入。

    只有本机才能改：这个开关决定别人能不能连进来，不能让已经连进来的客人自己放宽它。
    内置直连隧道的客人虽然源 IP 是回环，同样不算本机，见 ``net_access.peer_kind``。
    """
    client = request.client.host if request.client else None
    if net_access.peer_kind(client, request.headers) != "local":
        raise HTTPException(403, "只有房主本机可以修改联机设置")
    net_access.set_lan_enabled(data.enabled)
    return _status(request)


# ── 局域网接入名册 ────────────────────────────────────────────────────────────


class LanPeerOut(BaseModel):
    token: str
    status: str
    label: str
    claimed_label: str
    """对方自报的名字。界面必须标成「自称」——谁都能这么叫自己。"""
    last_addr: str
    last_seen: datetime
    first_seen: datetime
    online: bool
    """最近还在说话。见 ``lan_roster.ONLINE_WINDOW``。"""


class LanDecision(BaseModel):
    approved: bool
    label: str | None = None


class Knock(BaseModel):
    label: str = ""


class MyAccess(BaseModel):
    kind: str
    """local / lan / netlink——客人据此知道自己是哪种接入，只有 lan 才需要等批准。"""
    status: str
    """pending / approved / rejected。非 lan 接入恒为 approved。"""


@router.get("/peers", response_model=list[LanPeerOut])
def list_peers(request: Request, db: Session = Depends(get_db)) -> list[LanPeerOut]:
    _require_host(request)
    return [
        LanPeerOut(
            token=p.token, status=p.status, label=p.label,
            claimed_label=p.claimed_label, last_addr=p.last_addr,
            last_seen=p.last_seen, first_seen=p.first_seen,
            online=lan_roster.is_online(p),
        )
        for p in lan_roster.listing(db)
    ]


@router.post("/peers/{token}", response_model=LanPeerOut)
def decide_peer(
    token: str, data: LanDecision, request: Request, db: Session = Depends(get_db)
) -> LanPeerOut:
    _require_host(request)
    try:
        peer = lan_roster.decide(db, token, approved=data.approved, label=data.label)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    return LanPeerOut(
        token=peer.token, status=peer.status, label=peer.label,
        claimed_label=peer.claimed_label, last_addr=peer.last_addr,
        last_seen=peer.last_seen, first_seen=peer.first_seen,
        online=lan_roster.is_online(peer),
    )


@router.delete("/peers/{token}", status_code=204)
def forget_peer(token: str, request: Request, db: Session = Depends(get_db)) -> None:
    """从名册里抹掉。对方下次再来会重新排到门口——「重新认识一遍」。"""
    _require_host(request)
    lan_roster.forget(db, token)


@router.post("/knock", response_model=MyAccess)
def knock(
    data: Knock, request: Request, db: Session = Depends(get_db)
) -> MyAccess:
    """客人报个名字，好让房主认得出门口站着谁。免批准即可访问（见 ``_KNOCK_PATHS``）。"""
    token = request.headers.get("x-player-token") or ""
    if token and data.label.strip():
        lan_roster.claim_label(db, token, data.label)
    return _my_access(request, db, token)


@router.get("/me", response_model=MyAccess)
def my_access(request: Request, db: Session = Depends(get_db)) -> MyAccess:
    """客人查自己排到哪儿了。等批准期间前端轮询这个。"""
    return _my_access(request, db, request.headers.get("x-player-token") or "")


def _my_access(request: Request, db: Session, token: str) -> MyAccess:
    client = request.client.host if request.client else None
    kind = net_access.peer_kind(client, request.headers)
    if kind != "lan":
        return MyAccess(kind=kind, status="approved")
    return MyAccess(kind=kind, status=lan_roster.check_in(db, token, client))
