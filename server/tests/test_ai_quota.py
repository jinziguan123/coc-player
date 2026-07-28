"""房间级 AI 配额：ADR-007 里「房内玩家可用正常游戏动作烧房主额度」那条未决项。

按来源 IP 限速解决不了这个——那是防外人敲门，这里要防的是已经进门的人一直点单。
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import ai_quota
from app.services.generation_manager import GenerationManager


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_quota, "SETTINGS_FILE", tmp_path / "ai_quota.json")
    ai_quota.reset_cache()
    ai_quota.reset_counters()
    yield
    ai_quota.reset_cache()
    ai_quota.reset_counters()


async def _noop():
    await asyncio.sleep(0)


def _consume(room: str, times: int) -> list[bool]:
    """连续触发 times 次生成，返回每次是否被放行。"""
    async def run():
        results = []
        for _ in range(times):
            gm = GenerationManager()
            try:
                await gm.start(room, _noop())
                results.append(True)
            except ai_quota.QuotaExceeded:
                results.append(False)
        return results

    return asyncio.run(run())


def test_disabled_by_default():
    """单机自己玩不该被限——默认必须是关的。"""
    assert ai_quota.policy() == {"enabled": False, "limit": ai_quota.DEFAULT_LIMIT}
    assert ai_quota.remaining("room-a") is None
    assert all(_consume("room-a", 50))


def test_enabled_quota_blocks_after_limit():
    ai_quota.set_policy(True, "3/minute")
    results = _consume("room-a", 5)
    assert results == [True, True, True, False, False]


def test_quota_is_per_room():
    """一个房间刷爆了，别的房间照常——配额是房间级的。"""
    ai_quota.set_policy(True, "2/minute")
    assert _consume("room-a", 3) == [True, True, False]
    assert _consume("room-b", 2) == [True, True]


def test_remaining_reports_headroom():
    ai_quota.set_policy(True, "5/minute")
    assert ai_quota.remaining("room-a") == 5
    _consume("room-a", 2)
    assert ai_quota.remaining("room-a") == 3


def test_invalid_limit_falls_back_to_default():
    """坏配置不该让功能失效或房间卡死。"""
    ai_quota.SETTINGS_FILE.write_text('{"enabled": true, "limit": "每小时一百次"}', encoding="utf-8")
    ai_quota.reset_cache()
    assert ai_quota.policy()["limit"] == ai_quota.DEFAULT_LIMIT


def test_corrupt_file_disables_quota():
    """读不出来时按未启用处理——宁可不限，也不要莫名其妙把房间卡死。"""
    ai_quota.SETTINGS_FILE.write_text("{ 这不是 json", encoding="utf-8")
    ai_quota.reset_cache()
    assert ai_quota.policy()["enabled"] is False


# ── 端点 ──────────────────────────────────────────────────────────────────


def test_endpoint_roundtrip():
    client = TestClient(app)
    assert client.get("/api/settings/ai/quota").json() == {
        "enabled": False, "limit": ai_quota.DEFAULT_LIMIT,
    }

    body = client.put("/api/settings/ai/quota", json={"enabled": True, "limit": "20/minute"}).json()
    assert body == {"enabled": True, "limit": "20/minute"}
    assert ai_quota.policy()["enabled"] is True


def test_endpoint_is_host_only():
    """配额是保护房主钱包的策略，客人当然不能改（继承 ADR-007 的本机限制）。"""
    from app.services import net_access

    net_access._cached = True
    try:
        guest = TestClient(app, client=("192.168.1.50", 1))
        assert guest.get("/api/settings/ai/quota").status_code == 403
        assert guest.put("/api/settings/ai/quota", json={"enabled": False}).status_code == 403
    finally:
        net_access._cached = None
