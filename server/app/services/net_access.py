"""局域网可达性开关：监听地址与来源校验的唯一真源。

默认只监听回环。桌面版是本地优先应用，装上就在局域网里开着端口不合理，
也不符合 ADR-001「可信局域网」的前提——可信要靠房主显式授权，不能默认给出。

**两道闸，不是二选一：**

1. **监听地址**（socket 层，进程启动时决定）。关着就根本连不上，这是主闸，
   也是 ADR-001 的可信边界所在。socket 绑定不能热改，所以打开开关需要重启后端。
2. **来源校验**（HTTP 中间件，实时生效）。补主闸的两个空档：
   - 关掉开关立即对新请求生效，不必等重启；
   - 即便端口被误转发到公网，非私有网段的来源一律拒绝。

第 2 道是第 1 道的补充而非替代——安全边界始终是 socket，中间件只负责
「关掉立刻生效」和「防误暴露」。

**第三种来源：内置直连隧道（P-Net-4）。** Tauri 外壳里的 iroh 隧道把远端客人的
请求反代到本机 FastAPI，于是这些请求的源 IP 是 ``127.0.0.1``——而本模块此前把
「来自回环」直接等同于「房主本人」。不加区分的话，隧道客人会顺带拿到房主的
AI 配置（明文 API key）与限速豁免。``peer_kind`` 就是为此把来源从二值升级为三态，
见它的文档字符串。
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import secrets
import socket
from collections.abc import Mapping
from typing import Literal

from app.config import settings

logger = logging.getLogger(__name__)

# 与 db / ai_settings.json 同目录：dev 下是 server/，打包时是用户可写的 app-data。
# 必须是独立文件而不是数据库里的一行——``run_desktop.py`` 要在 FastAPI 起来之前
# 读到它来决定绑哪个地址。
SETTINGS_FILE = settings.db_path.parent / "net_settings.json"

LOOPBACK_HOST = "127.0.0.1"
ALL_INTERFACES_HOST = "0.0.0.0"  # noqa: S104 — 仅在房主显式打开开关后使用

# Tailscale / ZeroTier 一类覆盖网络用的 CGNAT 段。Python 的 ``is_private`` 不含它
# （100.64.0.0/10 在 RFC 6598 里是「共享地址空间」而非私有地址），但它正是跨网联机
# 推荐路径上的来源网段，必须显式放行。
_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")

# 进程内缓存：本进程里只有 ``set_lan_enabled`` 能改它，中间件每请求都要读，
# 不必每次落一次磁盘 IO。
_cached: bool | None = None


def lan_enabled() -> bool:
    """房主是否已允许局域网加入。文件不存在 / 读坏一律按「未允许」处理。"""
    global _cached
    if _cached is None:
        _cached = _read_from_disk()
    return _cached


def _read_from_disk() -> bool:
    try:
        data = json.loads(SETTINGS_FILE.read_text("utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, json.JSONDecodeError):
        logger.warning("联机设置文件读取失败，按「不允许局域网加入」处理：%s", SETTINGS_FILE)
        return False
    return bool(data.get("lan_enabled"))


def set_lan_enabled(enabled: bool) -> None:
    global _cached
    SETTINGS_FILE.write_text(
        json.dumps({"lan_enabled": bool(enabled)}, indent=2), encoding="utf-8"
    )
    _cached = bool(enabled)


def reset_cache() -> None:
    """丢弃进程内缓存，下次读盘。仅供测试与外部改文件后手动刷新。"""
    global _cached
    _cached = None


def bind_host() -> str:
    """后端该绑哪个地址。启动时读一次，之后改设置要重启才生效。"""
    return ALL_INTERFACES_HOST if lan_enabled() else LOOPBACK_HOST


def is_trusted_peer(host: str | None) -> bool:
    """请求来源是否放行。

    - 本机回环：永远放行（否则房主自己的界面都打不开）。
    - 未开局域网：只放行回环。
    - 已开局域网：放行私有网段与 CGNAT 段（覆盖网络），其余拒绝——
      端口被误转发到公网时，来自互联网的请求不会因为开关开着就被放进来。
    - 非 IP 的来源（TestClient 等 ASGI 直调）不属于网络对端，不拦。
    """
    if not host:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True
    if ip.is_loopback:
        return True
    if not lan_enabled():
        return False
    return is_trusted_peer_when_enabled(host)


def is_local_request(host: str | None) -> bool:
    """来源 IP 是否是回环。

    **做授权判断请用 ``peer_kind``，不要直接用这个。** 内置直连隧道接入后，
    「来自回环」不再等价于「房主本人」：隧道客人的源 IP 同样是 ``127.0.0.1``。
    本函数只回答 IP 这一层的事实，是 ``peer_kind`` 的组成部分。
    """
    if not host:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return True


PeerKind = Literal["local", "lan", "netlink"]

# 隧道标记。密钥由 Tauri 外壳在 spawn 后端 sidecar 时经环境变量传入，两端各持一份、
# 不落盘；每次启动重新随机。局域网上的人即便直连后端并伪造头，也出示不了它。
NETLINK_SECRET_ENV = "TRPG_NETLINK_SECRET"
NETLINK_SECRET_HEADER = "x-netlink-secret"
NETLINK_PEER_HEADER = "x-netlink-peer"


def peer_kind(host: str | None, headers: Mapping[str, str] | None = None) -> PeerKind:
    """请求来源的三态判定，授权决策的唯一真源。

    - ``local``：房主本人的界面（回环，且没有隧道标记）；
    - ``netlink``：经内置直连隧道进来的远端客人（回环 + 有效隧道标记）；
    - ``lan``：局域网或覆盖网络来的客人。

    **安全性依赖隧道侧的一条契约：反代必须先无条件剥离客户端自带的所有
    ``X-Netlink-*`` 头，再注入自己的。** 否则客人只要不发这个头就会被判成
    ``local``，反而升权成房主。后端这一侧无法自行验证这件事——它看到的
    回环请求，房主前端与隧道客人长得一模一样——所以剥离动作是隧道模块的
    不可省责任，那里有对应的测试盯着。

    没有设置密钥环境变量时（隧道未启用，含全部开发态与旧版本），任何头都不会
    被认作隧道标记，行为与三态改造前完全一致。
    """
    if headers is not None and is_local_request(host) and _has_netlink_mark(headers):
        return "netlink"
    return "local" if is_local_request(host) else "lan"


def _has_netlink_mark(headers: Mapping[str, str]) -> bool:
    expected = os.environ.get(NETLINK_SECRET_ENV)
    if not expected:
        return False
    presented = headers.get(NETLINK_SECRET_HEADER)
    if presented is None:
        return False
    return secrets.compare_digest(presented, expected)


def netlink_peer_id(headers: Mapping[str, str] | None) -> str | None:
    """隧道客人的对端公钥，仅在 ``peer_kind`` 已判定为 ``netlink`` 时有意义。

    限速要按它计数：隧道客人的源 IP 全是 ``127.0.0.1``，按 IP 计数会让所有远端
    玩家共用一个桶，一个人触顶全体被限。
    """
    if headers is None:
        return None
    return headers.get(NETLINK_PEER_HEADER)


# 探测本机地址用的 UDP 目标：分别指向常见私有网段与 tailnet。只用来问内核会选哪个
# 源地址，不产生流量。多个目标是必要的——装了接管默认路由的 VPN 时，单个目标会一律
# 返回 VPN 网卡地址（实测某 VPN 下探 10.x 与 100.x 都得到 28.0.0.1），漏掉真正的局域网地址。
_ROUTE_PROBES = ("10.255.255.255", "192.168.255.255", "172.31.255.255", "100.100.100.100")


def local_addresses() -> list[str]:
    """本机可供其他玩家填写的地址，用于界面展示。

    只返回 ``is_trusted_peer`` 会放行的地址——否则会出现「界面让房主把地址发出去、
    中间件又把从这个地址来的请求拒掉」的自相矛盾。VPN 网卡这类公网段地址因此被排除。
    """
    found: list[str] = []

    def add(addr: str | None) -> None:
        if addr and addr not in found and is_trusted_peer_when_enabled(addr):
            found.append(addr)

    for probe in _ROUTE_PROBES:
        add(_source_address_for(probe))
    # 路由探测只给得到「出口」地址，多网卡时不全；再按主机名枚举一遍补齐。
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        infos = []
    for addr in sorted({info[4][0] for info in infos}):
        add(addr)
    return found


def is_trusted_peer_when_enabled(host: str) -> bool:
    """开着局域网时这个来源会不会被放行（忽略回环与开关本身）。"""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if ip.is_loopback:
        return False
    return ip.is_private or ip in _CGNAT_NETWORK


def _source_address_for(probe: str) -> str | None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((probe, 1))  # UDP connect 只设置路由，不产生流量
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()
