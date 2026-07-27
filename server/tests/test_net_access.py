"""局域网可达性开关：监听地址、来源校验与设置端点。"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import net_access


@pytest.fixture
def client():
    """联机设置端点不碰数据库，直接用 app 即可。"""
    return TestClient(app)


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path, monkeypatch):
    """每个用例用独立的设置文件，别污染开发库旁边的真文件。"""
    monkeypatch.setattr(net_access, "SETTINGS_FILE", tmp_path / "net_settings.json")
    net_access.reset_cache()
    yield
    net_access.reset_cache()


def test_defaults_to_loopback_only():
    """没有设置文件时必须按「不允许」处理——默认不在局域网里开端口。"""
    assert net_access.lan_enabled() is False
    assert net_access.bind_host() == net_access.LOOPBACK_HOST


def test_enabling_switches_bind_host():
    net_access.set_lan_enabled(True)
    assert net_access.bind_host() == net_access.ALL_INTERFACES_HOST
    net_access.set_lan_enabled(False)
    assert net_access.bind_host() == net_access.LOOPBACK_HOST


def test_corrupt_settings_file_fails_closed():
    """设置文件损坏时按「不允许」处理，不能因为读不出来就放开。"""
    net_access.SETTINGS_FILE.write_text("{ 这不是 json", encoding="utf-8")
    assert net_access.lan_enabled() is False


def test_loopback_always_trusted():
    """房主自己的界面在任何设置下都必须能用。"""
    for host in ("127.0.0.1", "::1"):
        assert net_access.is_trusted_peer(host) is True
    net_access.set_lan_enabled(True)
    for host in ("127.0.0.1", "::1"):
        assert net_access.is_trusted_peer(host) is True


def test_lan_peer_rejected_until_enabled():
    assert net_access.is_trusted_peer("192.168.1.20") is False
    net_access.set_lan_enabled(True)
    assert net_access.is_trusted_peer("192.168.1.20") is True
    assert net_access.is_trusted_peer("10.0.0.7") is True


def test_public_peer_rejected_even_when_enabled():
    """端口被误转发到公网时，开关开着也不放行互联网来源。"""
    net_access.set_lan_enabled(True)
    assert net_access.is_trusted_peer("8.8.8.8") is False
    assert net_access.is_trusted_peer("2001:4860:4860::8888") is False


def test_cgnat_peer_trusted_when_enabled():
    """Tailscale 等覆盖网络走 100.64.0.0/10，Python 不认它是私有地址，必须显式放行。"""
    assert net_access.is_trusted_peer("100.101.102.103") is False
    net_access.set_lan_enabled(True)
    assert net_access.is_trusted_peer("100.101.102.103") is True


def test_non_ip_peer_not_blocked():
    """TestClient 一类 ASGI 直调没有真实网络对端，不该被当成外来请求拦掉。"""
    assert net_access.is_trusted_peer("testclient") is True
    assert net_access.is_trusted_peer(None) is True


def test_local_addresses_only_lists_addresses_the_gate_accepts():
    """界面不能让房主把一个中间件随后会拒绝的地址发出去（例如 VPN 网卡的公网段地址）。"""
    net_access.set_lan_enabled(True)
    for addr in net_access.local_addresses():
        assert net_access.is_trusted_peer(addr) is True


def test_only_loopback_may_change_setting():
    assert net_access.is_local_request("127.0.0.1") is True
    assert net_access.is_local_request("192.168.1.20") is False
    net_access.set_lan_enabled(True)
    # 开着局域网时客人能连进来，但仍然不能替房主改这个开关
    assert net_access.is_local_request("192.168.1.20") is False


def test_status_endpoint_reports_restart_required(client):
    r = client.get("/api/net")
    assert r.status_code == 200
    assert r.json() == {
        "lan_enabled": False,
        "listening_on_lan": False,
        "restart_required": False,
        "addresses": [],
    }

    r = client.post("/api/net/lan", json={"enabled": True})
    assert r.status_code == 200
    body = r.json()
    assert body["lan_enabled"] is True
    # 进程仍绑在回环上 → 提示需要重启
    assert body["listening_on_lan"] is False
    assert body["restart_required"] is True
