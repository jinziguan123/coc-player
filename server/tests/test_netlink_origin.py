"""内置直连隧道的来源判定（P-Net-4a）。

隧道把远端客人的请求以 ``127.0.0.1`` 反代进本机后端，而此前「来自回环」被直接
当作「房主本人」。不加区分的话，隧道客人会拿到房主的 AI 配置（明文 API key）
并豁免全部限速——即房间码枚举防护归零。

这些用例盯住三态判定本身，以及它在管理端点、限速两处消费点上的实际效果。
设计见 `docs/plans/2026-07-29-内置直连组网-design.md`。
"""

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.main import app
from app.services import net_access, rate_limit


SECRET = "test-netlink-secret"


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(net_access, "SETTINGS_FILE", tmp_path / "net_settings.json")
    net_access.reset_cache()
    yield
    net_access.reset_cache()


@pytest.fixture
def tunnel_up(monkeypatch):
    """模拟 Tauri 外壳已启用隧道：密钥经环境变量传给后端。"""
    monkeypatch.setenv(net_access.NETLINK_SECRET_ENV, SECRET)


def _marked(peer: str = "pubkey-abc") -> dict[str, str]:
    """隧道反代注入的头。"""
    return {
        net_access.NETLINK_SECRET_HEADER: SECRET,
        net_access.NETLINK_PEER_HEADER: peer,
    }


def _request(host: str, headers: dict[str, str] | None = None) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "client": (host, 5555),
            "headers": [
                (k.encode(), v.encode()) for k, v in (headers or {}).items()
            ],
        }
    )


# --- 跨进程契约 ---------------------------------------------------------


def test_wire_names_match_the_tunnel_side():
    """这三个名字是与 Rust 隧道的**跨进程约定**，不是内部实现细节。

    对应 `src-tauri/src/netlink/mod.rs` 的 `SECRET_ENV` 与
    `src-tauri/src/netlink/rewrite.rs` 的 `SECRET_HEADER` / `PEER_HEADER`。
    单改一侧不会报错，只会让隧道标记静默失效——于是所有远端客人被判成
    房主本机，安全边界无声消失。改名必须两侧一起改。
    """
    assert net_access.NETLINK_SECRET_ENV == "TRPG_NETLINK_SECRET"
    assert net_access.NETLINK_SECRET_HEADER == "x-netlink-secret"
    assert net_access.NETLINK_PEER_HEADER == "x-netlink-peer"


# --- 三态判定本身 -------------------------------------------------------


def test_loopback_without_mark_is_local(tunnel_up):
    """房主自己的界面：回环、不带标记。隧道开着也不能把他降权。"""
    assert net_access.peer_kind("127.0.0.1", {}) == "local"


def test_loopback_with_mark_is_netlink(tunnel_up):
    assert net_access.peer_kind("127.0.0.1", _marked()) == "netlink"


def test_wrong_secret_is_not_netlink(tunnel_up):
    """错误密钥不是「降级成 netlink」而是根本不认这个标记。"""
    headers = {net_access.NETLINK_SECRET_HEADER: "wrong"}
    assert net_access.peer_kind("127.0.0.1", headers) == "local"


def test_mark_from_non_loopback_is_still_lan(tunnel_up):
    """标记只在回环来源上有意义——隧道必然从本机反代进来。"""
    assert net_access.peer_kind("192.168.1.50", _marked()) == "lan"


def test_no_secret_env_means_marks_are_meaningless():
    """隧道未启用（含全部开发态与旧版本）时行为与改造前完全一致。"""
    assert net_access.peer_kind("127.0.0.1", _marked()) == "local"
    assert net_access.peer_kind("192.168.1.50", {}) == "lan"


def test_missing_headers_falls_back_to_ip_only(tunnel_up):
    """不传 headers 的调用方退回纯 IP 判定，不会误判成 netlink。"""
    assert net_access.peer_kind("127.0.0.1") == "local"
    assert net_access.peer_kind("192.168.1.50") == "lan"


def test_non_ip_peer_is_local(tunnel_up):
    """TestClient 一类 ASGI 直调没有真实网络对端，沿用既有的「不拦」语义。"""
    assert net_access.peer_kind("testclient", {}) == "local"
    assert net_access.peer_kind(None, {}) == "local"


# --- 管理端点：ADR-007 不能因为隧道而失效 -------------------------------


def _tunnel_guest() -> TestClient:
    return TestClient(app, client=("127.0.0.1", 5555), headers=_marked())


def test_tunnel_guest_cannot_read_host_api_key(tunnel_up):
    """最要命的一条：房主的明文 API key。"""
    r = _tunnel_guest().get("/api/settings/ai/profiles/whatever/key")
    assert r.status_code == 403


def test_tunnel_guest_cannot_list_host_ai_profiles(tunnel_up):
    assert _tunnel_guest().get("/api/settings/ai/profiles").status_code == 403


def test_tunnel_guest_cannot_delete_host_character(tunnel_up):
    assert _tunnel_guest().delete("/api/characters/whatever").status_code == 403


def test_tunnel_guest_cannot_change_net_settings(tunnel_up):
    """联机开关决定谁能连进来，已经连进来的人不能自己放宽它。"""
    r = _tunnel_guest().post("/api/net/lan", json={"enabled": True})
    assert r.status_code == 403


def test_host_itself_still_passes(tunnel_up):
    """隧道开着不能妨碍房主管理自己的机器。"""
    host = TestClient(app, client=("127.0.0.1", 5555))
    assert host.get("/api/settings/ai/profiles").status_code == 200


# --- 限速：隧道客人不得豁免 ---------------------------------------------


def test_tunnel_guest_is_not_exempt_from_rate_limit(tunnel_up):
    assert rate_limit.exempt_local(_request("127.0.0.1", _marked())) is False


def test_host_is_still_exempt(tunnel_up):
    assert rate_limit.exempt_local(_request("127.0.0.1")) is True


def test_tunnel_guests_get_separate_buckets(tunnel_up):
    """按公钥分桶：否则所有远端玩家共用一个桶，一个人触顶全场被限。"""
    a = rate_limit._key(_request("127.0.0.1", _marked("pubkey-a")))
    b = rate_limit._key(_request("127.0.0.1", _marked("pubkey-b")))
    assert a != b
    # 而且不能和房主本机落进同一个桶
    assert a != rate_limit._key(_request("127.0.0.1"))
