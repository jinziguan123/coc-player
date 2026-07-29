"""速率限制：房间码枚举与烧额度操作的闸门。

用 slowapi（`limits` 的 Starlette/FastAPI 封装）的**内存后端**。内存后端的通病是
多 worker 下每个进程各记一份、限流悄悄失效——但本应用是桌面单进程单 worker
（``run_desktop.py`` 直接 ``uvicorn.run(app)``），不存在「加了 --workers 4 就失效」
的路径，所以内存后端在这里是正确选择，也免掉 Redis 依赖。

按**请求来源 IP** 计数：房主本机的操作不该被别人的行为拖累，客人之间也应各记各的。
回环来源直接豁免——房主在自己机器上点多快都不该被限。
"""

from __future__ import annotations

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.services import net_access

# 房间码查询：防枚举。房间码是 8 位 base32（40 bit），配合这个限速，
# 在线爆破在任何有意义的时间尺度内都不可行。
ROOM_CODE_LOOKUP_LIMIT = "20/minute"

# 加入房间：即使猜中了房间码，也别想快速刷座位。
JOIN_LIMIT = "10/minute"

# 烧房主额度的操作（AI 生成角色等）。房内正常游戏动作不在此列——
# 那需要的是房间级配额，不是按 IP 限速，见 ADR-007 的未决项。
AI_TRIGGER_LIMIT = "10/minute"


def _key(request: Request) -> str:
    """计数桶。

    隧道客人不能按 IP 计数：他们经内置直连反代进来，源 IP 全是 ``127.0.0.1``，
    按 IP 算会让所有远端玩家共用一个桶——一个人触顶，全场被限。改用对端公钥。
    """
    client = request.client.host if request.client else None
    if net_access.peer_kind(client, request.headers) == "netlink":
        peer = net_access.netlink_peer_id(request.headers) or "unknown"
        return f"netlink:{peer}"
    return get_remote_address(request) or "unknown"


def exempt_local(request: Request) -> bool:
    """房主本机豁免。

    他在自己机器上操作自己的东西，限速只会碍事；而且桌面版前端与后端同源，
    正常使用本来就会有突发请求。参数名必须是 ``request``——slowapi 据此判断
    是否要把请求对象传进来（见 wrappers.py 的 ``_exempt_when_takes_request``）。

    **只豁免真·本机。** 内置直连隧道的客人源 IP 也是回环，若照旧只看 IP，
    他们会连房间码枚举限速一起豁免掉——那正是这个模块要防的事。
    """
    client = request.client.host if request.client else None
    return net_access.peer_kind(client, request.headers) == "local"


limiter = Limiter(
    key_func=_key,
    default_limits=[],           # 不设全局默认，只在明确标注的端点上限速
    headers_enabled=True,        # 回 X-RateLimit-* 头，便于排查
    # 必须按**端点**而不是按 URL 计数（slowapi 默认是 "url"）。房间码在路径里，
    # 按 URL 计数意味着「每猜一个码就是一个独立的计数桶」——正好把防枚举这件事
    # 变成完全无效。按端点计数后，同一来源对该端点的所有尝试共用一个桶。
    key_style="endpoint",
)
