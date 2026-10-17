"""联机鲁棒性：一个人掉线 / 挂机 / 误操作不卡死整局，也不能越权。

覆盖：生成中重复提交给 409、重新生成限房主（房主离线放开）、真人 KP 可强制推进、
结束投票掉线豁免与断线重判、回合进度口径统一、开局后禁止新入座、无主卡不转归属、
角色卡读取鉴权、AI 接口限速、踢人掐 /live、删房取消生成并关房间。
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import get_db
from app.main import app
from app.models import Base, Character, GameSession, Module, SessionParticipant
from app.services import (
    ai_character_service,
    net_access,
    rate_limit,
    room_sync,
    session_service,
)
from app.services.generation_manager import AlreadyGenerating, generation_manager
from app.services.room_hub import RoomHub, room_hub, stream_room

LAN = ("192.168.1.50", 5555)


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'robust.db'}", connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    def override():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    monkeypatch.setattr(net_access, "SETTINGS_FILE", tmp_path / "net_settings.json")
    net_access.reset_cache()
    net_access.set_lan_enabled(True)
    rate_limit.limiter.reset()
    yield factory
    app.dependency_overrides.clear()
    rate_limit.limiter.reset()


def _two_player_room(factory, *, kp_mode: str = "ai", status: str = "active") -> dict:
    """房主（host，0 号席 hero）+ 客人（guest，1 号席 mate），都已认领。"""
    db = factory()
    module = Module(title="鲁棒性", rule_system="coc", scenes=[{"id": "hall", "title": "门厅"}], npcs=[])
    hero = Character(name="房主角色", rule_system="coc", is_player=True, owner_token="host")
    mate = Character(name="客人角色", rule_system="coc", is_player=True, owner_token="guest")
    db.add_all([module, hero, mate])
    db.flush()
    session = GameSession(
        module_id=module.id, player_character_id=hero.id, current_scene_id="hall",
        status=status, kp_mode=kp_mode, host_token="host", identity_version=2,
        world_state={"visited_scenes": ["hall"]},
    )
    db.add(session)
    db.flush()
    db.add_all([
        SessionParticipant(session_id=session.id, character_id=hero.id, role="human",
                           is_primary=True, owner_token="host", claimed=True, ready=True,
                           seat_order=0, identity_version=2),
        SessionParticipant(session_id=session.id, character_id=mate.id, role="human",
                           is_primary=False, owner_token="guest", claimed=True, ready=True,
                           seat_order=1, identity_version=2),
    ])
    db.commit()
    ids = {"sid": session.id, "hero": hero.id, "mate": mate.id, "module": module.id}
    db.close()
    return ids


class _Online:
    """把一组 token 挂成在线（订阅 room_hub），用完退订。"""

    def __init__(self, sid: str, *tokens: str):
        self.sid = sid
        self.subs = [(room_hub.subscribe(sid, t), t) for t in tokens]

    def close(self):
        for q, t in self.subs:
            room_hub.unsubscribe(self.sid, q, t)


# ── 卡死：生成中重复提交 → 409 ─────────────────────────────────────────

def test_start_while_generating_raises_already_generating():
    async def run():
        async def slow():
            await asyncio.sleep(10)

        generation_manager.start("room-dup", slow())
        try:
            async def other():
                return None
            with pytest.raises(AlreadyGenerating):
                generation_manager.start("room-dup", other())
        finally:
            await generation_manager.cancel("room-dup")

    asyncio.run(run())
    # 兼容既有的 except ValueError 调用点（combat 的余波调度）
    assert issubclass(AlreadyGenerating, ValueError)


def test_luck_decision_while_generating_is_409(env, monkeypatch):
    ids = _two_player_room(env)
    db = env()
    session_service.set_pending_luck(db, ids["sid"], {"char_id": ids["mate"], "offer": {"cost": 1}})
    db.close()
    monkeypatch.setattr(generation_manager, "is_generating", lambda _sid: True)
    r = TestClient(app).post(f"/api/sessions/{ids['sid']}/luck-decision",
                             json={"spend": True}, headers={"X-Player-Token": "guest"})
    assert r.status_code == 409


def test_generation_race_maps_to_409_not_500(env, monkeypatch):
    """前置检查与 start 之间被别人抢先（竞态）：兜底的异常处理器给 409。"""
    ids = _two_player_room(env)
    db = env()
    session_service.set_pending_luck(db, ids["sid"], {"char_id": ids["mate"], "offer": {"cost": 1}})
    db.close()

    def boom(_sid, coro, prelude=None):
        coro.close()
        raise AlreadyGenerating("KP 正在叙事，请稍候")

    monkeypatch.setattr(generation_manager, "start", boom)
    r = TestClient(app).post(f"/api/sessions/{ids['sid']}/luck-decision",
                             json={"spend": False}, headers={"X-Player-Token": "guest"})
    assert r.status_code == 409


# ── 越权：重新生成限房主，房主离线时放开 ──────────────────────────────

@pytest.fixture
def stub_regenerate(monkeypatch):
    calls = []

    async def no_cancel(_sid):
        return None

    def fake_start(sid, coro, prelude=None):
        coro.close()
        calls.append(sid)

    monkeypatch.setattr(generation_manager, "cancel", no_cancel)
    monkeypatch.setattr(generation_manager, "start", fake_start)
    monkeypatch.setattr(session_service, "rollback_last_kp_output", lambda db, sid: 0)
    return calls


def test_regenerate_rejects_guest_while_host_online(env, stub_regenerate):
    ids = _two_player_room(env)
    online = _Online(ids["sid"], "host", "guest")
    try:
        r = TestClient(app).post(f"/api/sessions/{ids['sid']}/regenerate",
                                 headers={"X-Player-Token": "guest"})
    finally:
        online.close()
    assert r.status_code == 403
    assert stub_regenerate == []


def test_regenerate_allows_guest_when_host_offline(env, stub_regenerate):
    ids = _two_player_room(env)
    online = _Online(ids["sid"], "guest")
    try:
        r = TestClient(app).post(f"/api/sessions/{ids['sid']}/regenerate",
                                 headers={"X-Player-Token": "guest"})
    finally:
        online.close()
    assert r.status_code == 200
    assert stub_regenerate == [ids["sid"]]


def test_regenerate_allows_host(env, stub_regenerate):
    ids = _two_player_room(env)
    online = _Online(ids["sid"], "host", "guest")
    try:
        r = TestClient(app).post(f"/api/sessions/{ids['sid']}/regenerate",
                                 headers={"X-Player-Token": "host"})
    finally:
        online.close()
    assert r.status_code == 200


# ── 卡死：真人 KP 可以强制推进 ────────────────────────────────────────

def test_human_kp_who_is_not_host_can_force_advance(env, monkeypatch):
    ids = _two_player_room(env, kp_mode="human")
    db = env()
    db.add(SessionParticipant(session_id=ids["sid"], character_id=None, role="kp",
                              is_primary=False, owner_token="kp", claimed=True, ready=True,
                              seat_order=2, identity_version=2))
    db.commit()
    db.close()
    monkeypatch.setattr("app.api.chat._continue_human_kp_turn", lambda *a, **k: None)
    c = TestClient(app)
    assert c.post(f"/api/sessions/{ids['sid']}/force-advance",
                  headers={"X-Player-Token": "kp"}).status_code == 200
    assert c.post(f"/api/sessions/{ids['sid']}/force-advance",
                  headers={"X-Player-Token": "guest"}).status_code == 403


# ── 卡死：结束投票掉线豁免 ──────────────────────────────────────────

def test_end_vote_exempts_offline_player(env):
    ids = _two_player_room(env)
    db = env()
    ended, vote = session_service.cast_end_vote_for_actor(db, ids["sid"], ids["hero"], {"host"})
    assert ended is True
    assert db.get(GameSession, ids["sid"]).status == "ended"
    db.close()


def test_end_vote_still_needs_online_players(env):
    ids = _two_player_room(env)
    db = env()
    ended, vote = session_service.cast_end_vote_for_actor(
        db, ids["sid"], ids["hero"], {"host", "guest"},
    )
    assert ended is False
    assert vote["agreed_count"] == 1 and vote["total"] == 2
    db.close()


def test_end_vote_public_marks_offline_as_exempt(env):
    ids = _two_player_room(env)
    db = env()
    vote = session_service.end_vote_public(db, ids["sid"], {"host"})
    exempt = {v["character_id"]: v["exempt"] for v in vote["voters"]}
    assert exempt == {ids["hero"]: False, ids["mate"]: True}
    assert vote["total"] == 1
    db.close()


def test_end_vote_reevaluated_when_holdout_disconnects(env):
    """房主同意后客人掉线：不必等任何人再点一次，断线那一刻就结束。"""
    ids = _two_player_room(env)
    db = env()
    session_service.cast_end_vote_for_actor(db, ids["sid"], ids["hero"], {"host", "guest"})
    assert session_service.reevaluate_end_vote(db, ids["sid"], {"host", "guest"})[0] is False
    ended, _ = session_service.reevaluate_end_vote(db, ids["sid"], {"host"})
    assert ended is True
    assert db.get(GameSession, ids["sid"]).status == "ended"
    db.close()


def test_reevaluate_without_open_vote_is_noop(env):
    ids = _two_player_room(env)
    db = env()
    assert session_service.reevaluate_end_vote(db, ids["sid"], {"host"}) is None
    assert db.get(GameSession, ids["sid"]).status == "active"
    db.close()


def test_end_vote_is_in_sync_snapshot(env):
    ids = _two_player_room(env)
    db = env()
    session_service.cast_end_vote_for_actor(db, ids["sid"], ids["hero"], {"host", "guest"})
    online = _Online(ids["sid"], "host", "guest")
    try:
        snap = room_sync.snapshot(db, db.get(GameSession, ids["sid"]), ["end_vote"])
    finally:
        online.close()
    assert snap["end_vote"]["open"] is True
    assert snap["end_vote"]["agreed_count"] == 1 and snap["end_vote"]["total"] == 2
    db.close()


def test_stream_room_notifies_leave_listeners():
    hub_calls: list[str] = []
    room_hub.add_leave_listener(hub_calls.append)

    async def run():
        q = room_hub.subscribe("room-leave", "tok")
        q.put_nowait(None)
        async for _ in stream_room("room-leave", q, "tok", heartbeat=0.01):
            pass

    try:
        asyncio.run(run())
    finally:
        room_hub._leave_listeners.remove(hub_calls.append)
    assert hub_calls == ["room-leave"]


# ── 体验：回合进度口径统一（掉线豁免） ─────────────────────────────────

def test_live_turn_state_uses_online_set_everywhere(env):
    ids = _two_player_room(env)
    db = env()
    online = _Online(ids["sid"], "host")
    try:
        # 客人掉线：广播与快照都不该把他算进分母
        assert session_service.live_turn_state(db, ids["sid"])["total"] == 1
        snap = room_sync.snapshot(db, db.get(GameSession, ids["sid"]), ["turn"])
        assert snap["turn"]["total"] == 1
        # 请求方本人即使 /live 瞬断也要计入
        assert session_service.live_turn_state(db, ids["sid"], "guest")["total"] == 2
    finally:
        online.close()
    db.close()


# ── 越权：claim ────────────────────────────────────────────────────

def _room_with_open_seat(factory, *, status: str, reserved_token: str | None = None) -> dict:
    db = factory()
    module = Module(title="空席", rule_system="coc", scenes=[], npcs=[])
    hero = Character(name="房主角色", rule_system="coc", is_player=True, owner_token="host")
    loose = Character(name="无主卡", rule_system="coc", is_player=True)
    db.add_all([module, hero, loose])
    db.flush()
    session = GameSession(module_id=module.id, player_character_id=hero.id, status=status,
                          host_token="host", identity_version=2, world_state={})
    db.add(session)
    db.flush()
    db.add_all([
        SessionParticipant(session_id=session.id, character_id=hero.id, role="human",
                           is_primary=True, owner_token="host", claimed=True, ready=True,
                           seat_order=0, identity_version=2),
        SessionParticipant(session_id=session.id, character_id=None, role="human",
                           is_primary=False, owner_token=reserved_token,
                           claimed=bool(reserved_token), ready=False, seat_order=1,
                           identity_version=2),
    ])
    db.commit()
    ids = {"sid": session.id, "loose": loose.id}
    db.close()
    return ids


def test_claim_open_seat_after_start_is_rejected(env):
    ids = _room_with_open_seat(env, status="active")
    db = env()
    with pytest.raises(ValueError, match="游戏已经开始"):
        session_service.claim_seat(db, ids["sid"], 1, ids["loose"], "stranger")
    db.close()


def test_reserved_player_can_still_pick_character_after_start(env):
    ids = _room_with_open_seat(env, status="active", reserved_token="guest")
    db = env()
    session_service.claim_seat(db, ids["sid"], 1, ids["loose"], "guest")
    seat = next(p for p in session_service.get_participants(db, ids["sid"]) if p.seat_order == 1)
    assert seat.character_id == ids["loose"]
    db.close()


def test_guest_claiming_unowned_card_does_not_take_ownership(env):
    ids = _room_with_open_seat(env, status="setup")
    db = env()
    session_service.claim_seat(db, ids["sid"], 1, ids["loose"], "guest")
    assert db.get(Character, ids["loose"]).owner_token is None
    db.close()


def test_host_claiming_unowned_card_takes_ownership(env):
    ids = _room_with_open_seat(env, status="setup")
    db = env()
    # 房主让出主角席、改坐 1 号席（新模型房主身份独立于席位）
    seat0 = next(p for p in session_service.get_participants(db, ids["sid"]) if p.seat_order == 0)
    seat0.owner_token = None
    seat0.claimed = False
    db.commit()
    session_service.claim_seat(db, ids["sid"], 1, ids["loose"], "host")
    assert db.get(Character, ids["loose"]).owner_token == "host"
    db.close()


# ── 越权：GET /characters/{id} ─────────────────────────────────────────

def test_guest_reads_own_teammate_and_unowned_cards_but_not_strangers(env):
    ids = _two_player_room(env)
    db = env()
    stranger = Character(name="陌生人的卡", rule_system="coc", owner_token="stranger")
    loose = Character(name="无主卡", rule_system="coc")
    db.add_all([stranger, loose])
    db.commit()
    stranger_id, loose_id = stranger.id, loose.id
    db.close()

    guest = TestClient(app, client=LAN)
    h = {"X-Player-Token": "guest"}
    assert guest.get(f"/api/characters/{ids['mate']}", headers=h).status_code == 200
    assert guest.get(f"/api/characters/{ids['hero']}", headers=h).status_code == 200  # 同桌队友
    assert guest.get(f"/api/characters/{loose_id}", headers=h).status_code == 200
    assert guest.get(f"/api/characters/{stranger_id}", headers=h).status_code == 403
    assert guest.get(f"/api/characters/{stranger_id}").status_code == 403
    # 房主本机不受限
    assert TestClient(app).get(f"/api/characters/{stranger_id}").status_code == 200


# ── 越权：AI 接口限速（客人限、本机豁免） ─────────────────────────────

def test_ai_generate_is_rate_limited_for_guests_only(env, monkeypatch):
    async def fake_generate(db, module_id, hint, is_player):
        return {"name": "生成角色"}

    monkeypatch.setattr(ai_character_service, "generate_ai_character", fake_generate)
    limit = int(rate_limit.AI_TRIGGER_LIMIT.split("/")[0])
    body = {"module_id": "m"}

    guest = TestClient(app, client=LAN)
    codes = [guest.post("/api/characters/ai-generate", json=body).status_code
             for _ in range(limit + 1)]
    assert codes[:limit] == [200] * limit
    assert codes[-1] == 429

    local = TestClient(app)
    assert all(local.post("/api/characters/ai-generate", json=body).status_code == 200
               for _ in range(limit + 1))


def test_evaluate_is_rate_limited_for_guests(env):
    limit = int(rate_limit.AI_TRIGGER_LIMIT.split("/")[0])
    guest = TestClient(app, client=LAN)
    # 模组不存在 → 404，但限速在进入函数之前就计数
    codes = [guest.post("/api/characters/evaluate",
                        json={"module_id": "missing", "name": "x"}).status_code
             for _ in range(limit + 1)]
    assert codes[-1] == 429


# ── 踢人掐 /live、删房关房间并取消生成 ────────────────────────────────

def test_disconnect_room_token_only_cuts_that_room():
    hub = RoomHub()
    a = hub.subscribe("room-a", "tok")
    b = hub.subscribe("room-b", "tok")
    other = hub.subscribe("room-a", "other")
    assert hub.disconnect_room_token("room-a", "tok") == 1
    assert a.get_nowait() is None
    assert b.empty() and other.empty()


def test_close_room_cuts_every_member():
    hub = RoomHub()
    qs = [hub.subscribe("room-x", t) for t in ("a", "b", None)]
    assert hub.close_room("room-x") == 3
    assert all(q.get_nowait() is None for q in qs)


def test_kick_cuts_kicked_players_live(env):
    ids = _two_player_room(env)
    online = _Online(ids["sid"], "host", "guest")
    try:
        r = TestClient(app).post(f"/api/sessions/{ids['sid']}/kick/1",
                                 headers={"X-Player-Token": "host"})
        assert r.status_code == 200
        host_q, guest_q = online.subs[0][0], online.subs[1][0]
        assert guest_q.get_nowait() is None
        assert all(item is not None for item in list(host_q._queue))
    finally:
        online.close()
    # 被踢者开局后不再能读这个房间 → 前端重连吃 403 后停止
    db = env()
    assert not session_service.can_view_session(db, ids["sid"], "guest")
    db.close()


def test_delete_room_cancels_generation_then_closes_live(env, monkeypatch):
    ids = _two_player_room(env)
    order: list[str] = []

    async def fake_cancel(sid):
        order.append("cancel")

    real_delete = session_service.delete_session

    def tracked_delete(db, sid):
        order.append("delete")
        return real_delete(db, sid)

    monkeypatch.setattr(generation_manager, "cancel", fake_cancel)
    monkeypatch.setattr(session_service, "delete_session", tracked_delete)
    online = _Online(ids["sid"], "guest")
    try:
        r = TestClient(app).delete(f"/api/sessions/{ids['sid']}",
                                   headers={"X-Player-Token": "host"})
        assert r.status_code == 200
        assert order == ["cancel", "delete"]
        assert online.subs[0][0].get_nowait() is None
    finally:
        online.close()
