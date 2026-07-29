"""联机可达性设置：查询/切换「允许局域网加入」。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.services import net_access

router = APIRouter(prefix="/api/net", tags=["net"])


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
