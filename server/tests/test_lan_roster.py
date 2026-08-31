"""局域网接入名册：陌生设备要房主先点头。

标 ``lan_roster_live`` 让开 conftest 的放行夹具——这个文件测的就是那道闸本身。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models.lan_peer import LanPeer
from app.services import lan_roster, net_access

pytestmark = pytest.mark.lan_roster_live

HOST = ("127.0.0.1", 1)
GUEST_IP = "192.168.1.50"


@pytest.fixture(autouse=True)
def _lan_on(tmp_path, monkeypatch):
    monkeypatch.setattr(net_access, "_STATE_FILE", tmp_path / "net.json", raising=False)
    net_access.set_lan_enabled(True)
    net_access.reset_cache()
    lan_roster.reset_cache()
    yield
    net_access.set_lan_enabled(False)
    net_access.reset_cache()


def _guest(token: str, ip: str = GUEST_IP) -> TestClient:
    client = TestClient(app, client=(ip, 5555))
    client.headers.update({"X-Player-Token": token})
    return client


def _host() -> TestClient:
    return TestClient(app, client=HOST)


def test_陌生设备被挡在门外并登记到门口():
    guest = _guest("tok-stranger")
    res = guest.get("/api/sessions")
    assert res.status_code == 403
    assert res.json()["code"] == "lan_pending"

    peers = _host().get("/api/net/peers").json()
    assert [p["token"] for p in peers] == ["tok-stranger"]
    assert peers[0]["status"] == "pending"
    assert peers[0]["last_addr"] == GUEST_IP


def test_敲门与自查在获批之前也能用():
    """少了这几个口子，「敲门」就无从发生：客人报不上名字，房主也没得批。"""
    guest = _guest("tok-knock")
    assert guest.get("/api/health").status_code == 200

    res = guest.post("/api/net/knock", json={"label": "老王的笔记本"})
    assert res.status_code == 200
    assert res.json() == {"kind": "lan", "status": "pending"}

    assert guest.get("/api/net/me").json()["status"] == "pending"
    peers = _host().get("/api/net/peers").json()
    assert peers[0]["claimed_label"] == "老王的笔记本"


def test_房主批准后立刻放行():
    guest = _guest("tok-ok")
    assert guest.get("/api/sessions").status_code == 403

    host = _host()
    assert host.post("/api/net/peers/tok-ok", json={"approved": True}).status_code == 200

    assert guest.get("/api/sessions").status_code == 200
    assert guest.get("/api/net/me").json()["status"] == "approved"


def test_没填备注就采用对方自称():
    """房主填的优先——自称不可信；但多数时候房主懒得填，自称也比一串 token 好认。"""
    guest = _guest("tok-label")
    guest.post("/api/net/knock", json={"label": "自称小李"})

    host = _host()
    peer = host.post("/api/net/peers/tok-label", json={"approved": True}).json()
    assert peer["label"] == "自称小李"

    guest2 = _guest("tok-label2")
    guest2.post("/api/net/knock", json={"label": "自称小张"})
    peer2 = host.post(
        "/api/net/peers/tok-label2", json={"approved": True, "label": "隔壁老张"}
    ).json()
    assert peer2["label"] == "隔壁老张"


def test_被拒绝的人不会重新排到门口():
    """拒绝要留记录。删掉的话对方下次请求又是陌生设备，房主被反复打扰。"""
    guest = _guest("tok-no")
    guest.get("/api/sessions")

    host = _host()
    host.post("/api/net/peers/tok-no", json={"approved": False})

    res = guest.get("/api/sessions")
    assert res.status_code == 403
    assert res.json()["code"] == "lan_rejected"

    peers = host.get("/api/net/peers").json()
    assert [p["status"] for p in peers] == ["rejected"]


def test_吊销已批准的人下一个请求就进不来():
    guest = _guest("tok-revoke")
    guest.get("/api/sessions")                      # 先排到门口
    host = _host()
    host.post("/api/net/peers/tok-revoke", json={"approved": True})
    assert guest.get("/api/sessions").status_code == 200

    host.post("/api/net/peers/tok-revoke", json={"approved": False})
    assert guest.get("/api/sessions").status_code == 403


def test_抹掉记录后对方重新排到门口():
    guest = _guest("tok-forget")
    guest.get("/api/sessions")
    host = _host()
    host.post("/api/net/peers/tok-forget", json={"approved": True})
    guest.get("/api/sessions")

    assert host.delete("/api/net/peers/tok-forget").status_code == 204
    assert host.get("/api/net/peers").json() == []

    assert guest.get("/api/sessions").json()["code"] == "lan_pending"


def test_不带_token_的局域网请求一律拦下():
    """不报身份就没法被批准，也不该被放行。"""
    anon = TestClient(app, client=(GUEST_IP, 5555))
    assert anon.get("/api/sessions").status_code == 403


def test_房主本机不过这道闸():
    assert _host().get("/api/sessions").status_code == 200


def test_客人管不了名册():
    guest = _guest("tok-nosy")
    guest.get("/api/sessions")
    _host().post("/api/net/peers/tok-nosy", json={"approved": True})
    assert guest.get("/api/net/peers").status_code == 403
    assert guest.post("/api/net/peers/tok-other", json={"approved": True}).status_code == 403
    assert guest.delete("/api/net/peers/tok-other").status_code == 403


def test_在线按最近是否还在说话算():
    _guest("tok-online").get("/api/sessions")
    _host().post("/api/net/peers/tok-online", json={"approved": True})
    _guest("tok-online").get("/api/sessions")
    assert _host().get("/api/net/peers").json()[0]["online"] is True

    db = SessionLocal()
    try:
        peer = db.get(LanPeer, "tok-online")
        peer.last_seen = datetime.utcnow() - lan_roster.ONLINE_WINDOW - timedelta(seconds=5)
        db.commit()
    finally:
        db.close()
    assert _host().get("/api/net/peers").json()[0]["online"] is False


def test_只能批门口站着的人():
    """房主批的是门口那位。凭空批一个 token 没有意义——他根本不知道别人的 token。"""
    assert _host().post(
        "/api/net/peers/tok-never-came", json={"approved": True}
    ).status_code == 404


def test_门口的排在名册前面():
    host = _host()
    for tok in ("tok-a", "tok-b", "tok-c"):
        _guest(tok).get("/api/sessions")
    host.post("/api/net/peers/tok-a", json={"approved": True})
    host.post("/api/net/peers/tok-c", json={"approved": False})

    assert [p["status"] for p in host.get("/api/net/peers").json()] == [
        "pending", "approved", "rejected",
    ]


def test_吊销要真的把实时连接掐掉():
    """403 只挡得住下一个 HTTP 请求。/live 是条已经建好的 SSE，不掐它，被拒的人还能
    接着看这一桌在演什么——那样「吊销」就只是个名义动作。"""
    import asyncio

    from app.services.room_hub import room_hub

    async def scenario() -> tuple[int, object]:
        q = room_hub.subscribe("room-1", token="tok-live")
        db = SessionLocal()
        try:
            db.add(LanPeer(token="tok-live", status="approved"))
            db.commit()
            lan_roster.decide(db, "tok-live", approved=False)
        finally:
            db.close()
        return q.qsize(), q.get_nowait()

    size, first = asyncio.run(scenario())
    assert size == 1
    assert first is None      # stream_room 收到 None 即结束这条连接


def test_没被吊销的人的连接不受牵连():
    import asyncio

    from app.services.room_hub import room_hub

    async def scenario() -> object:
        mine = room_hub.subscribe("room-2", token="tok-keep")
        room_hub.subscribe("room-2", token="tok-drop")
        db = SessionLocal()
        try:
            db.add_all([
                LanPeer(token="tok-keep", status="approved"),
                LanPeer(token="tok-drop", status="approved"),
            ])
            db.commit()
            lan_roster.decide(db, "tok-drop", approved=False)
        finally:
            db.close()
        return mine.qsize()

    assert asyncio.run(scenario()) == 0
