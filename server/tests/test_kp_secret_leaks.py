"""KP 专属信息不外泄：玩家与客人在任何下发路径都拿不到守秘人资料。

「KP 视角」只有两种人：本机房主（``peer_kind == "local"``，数据本来就在他机器上），
以及本局真人 KP 席位的持有者。其余——局域网 / 直连隧道进来的客人、已入座的玩家、
拿着房间码的陌生人——只能拿到玩家可见的那一份。

每条用例对应一条曾经真实存在的泄露路径。
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import get_db
from app.main import app
from app.models import Base, Character, Module, SessionParticipant
from app.services import net_access, session_service

TRUTH = "真相：管家就是邪教首领"
NPC_SECRET = "他其实早已死去"
CLUE_TEXT = "地窖里的血迹"
ENDING_TEXT = "管家被揭穿后自焚"
TRIGGER_TEXT = "管家身份暴露"
HANDOUT_TEXT = "管家写给教团的密信"
BACKSTAGE_TEXT = "管家趁夜把尸体拖进了地窖"


@pytest.fixture(autouse=True)
def _lan_open(tmp_path, monkeypatch):
    """打开局域网，让 192.168.x 来的客人越过来源闸、走到权限这一层。"""
    monkeypatch.setattr(net_access, "SETTINGS_FILE", tmp_path / "net_settings.json")
    net_access.reset_cache()
    net_access.set_lan_enabled(True)
    yield
    net_access.reset_cache()


def _module() -> Module:
    return Module(
        title="管家之谜",
        rule_system="coc",
        description="一座古宅里的失踪案。",
        world_setting={
            "era": "1920s", "player_count": "2-4",
            "intro": "雨夜，你们来到古宅。", "player_brief": "受托调查失踪的女仆。",
            "sandbox_backdrop": "/images/secret-map.png",
            "kp_notes": TRUTH,
        },
        character_guidance={"summary": "适合调查型角色"},
        scenes=[{"id": "hall", "title": "门厅", "description": "门厅", "kp_notes": TRUTH}],
        npcs=[{
            "id": "butler", "name": "管家", "description": "一位沉默的中年男子",
            "secrets": NPC_SECRET,
        }],
        clues=[{"id": "c1", "name": "血迹", "description": CLUE_TEXT}],
        triggers=[{"when": TRIGGER_TEXT, "set_flag": "butler_exposed"}],
        handouts=[{"id": "h1", "title": "密信", "content": HANDOUT_TEXT}],
        endings=[{"id": "e1", "name": "火葬", "when": ENDING_TEXT}],
        truth=TRUTH,
    )


_SECRET_WORLD_STATE = {
    "backstage": {"last_run_seq": 3, "note": BACKSTAGE_TEXT},
    "npc_memory": {"butler": {"insight": "看穿了调查员的谎言"}},
    "clue_ledger": {"c1": {"status": "found"}},
    "improvised_npcs": {"老园丁": {"mentions": 3, "card": {"secrets": NPC_SECRET}}},
    "team_memory": {"x": "心事"},
    "flags": {"butler_exposed": True},
    "story_summary": BACKSTAGE_TEXT,
    "ending_reached": {"id": "e1", "name": "火葬", "reason": ENDING_TEXT},
}


@pytest.fixture
def env(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'leaks.db'}", connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    def override_get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    db = factory()
    module = _module()
    host_char = Character(name="房主角色", rule_system="coc", is_player=True)
    guest_char = Character(name="客人角色", rule_system="coc", is_player=True)
    kp_hero = Character(name="真人局主角", rule_system="coc", is_player=True)
    kp_player = Character(name="真人局玩家", rule_system="coc", is_player=True)
    db.add_all([module, host_char, guest_char, kp_hero, kp_player])
    db.commit()

    ai_session = session_service.create_session(
        db, module.id,
        [{"character_id": host_char.id, "role": "human", "is_primary": True}],
        creator_token="host-token",
    )
    ai_session.world_state = dict(_SECRET_WORLD_STATE)
    ai_session.status = "active"
    db.add(SessionParticipant(
        session_id=ai_session.id, character_id=guest_char.id, role="human",
        seat_order=9, owner_token="guest-token", claimed=True, ready=True,
    ))

    kp_session = session_service.create_session(
        db, module.id,
        [{"character_id": kp_hero.id, "role": "human", "is_primary": True}],
        creator_token="kp-token", kp_mode="human",
    )
    kp_session.world_state = dict(_SECRET_WORLD_STATE)
    kp_session.status = "active"
    # 真人 KP 局里的普通玩家（与 KP 不同 token）
    db.add(SessionParticipant(
        session_id=kp_session.id, character_id=kp_player.id, role="human",
        seat_order=9, owner_token="player-token", claimed=True, ready=True,
    ))
    db.commit()
    ids = {
        "module": module.id, "ai": ai_session.id, "ai_code": ai_session.room_code,
        "kp": kp_session.id,
    }
    db.close()
    yield ids, factory
    app.dependency_overrides.clear()


def _guest(token: str | None = None) -> TestClient:
    c = TestClient(app, client=("192.168.1.50", 5555))
    if token:
        c.headers["X-Player-Token"] = token
    return c


def _host(token: str | None = "host-token") -> TestClient:
    c = TestClient(app, client=("127.0.0.1", 5555), base_url="http://127.0.0.1:8000")
    if token:
        c.headers["X-Player-Token"] = token
    return c


def _assert_no_secrets(text: str) -> None:
    for secret in (TRUTH, NPC_SECRET, CLUE_TEXT, ENDING_TEXT, TRIGGER_TEXT, HANDOUT_TEXT,
                   BACKSTAGE_TEXT, "secret-map.png"):
        assert secret not in text, f"泄露：{secret}"


# ── 1. 模组接口 ──────────────────────────────────────────────────────

def test_guest_module_list_hides_keeper_material(env):
    r = _guest("guest-token").get("/api/modules")
    assert r.status_code == 200
    _assert_no_secrets(r.text)
    mod = r.json()[0]
    # 玩家可见字段照常给：大厅简介、人数范围、车卡建议、车卡的年代
    assert mod["title"] == "管家之谜"
    assert mod["description"] == "一座古宅里的失踪案。"
    assert mod["world_setting"]["player_count"] == "2-4"
    assert mod["world_setting"]["era"] == "1920s"
    assert mod["world_setting"]["intro"] == "雨夜，你们来到古宅。"
    assert mod["character_guidance"]["summary"] == "适合调查型角色"


def test_guest_module_detail_hides_keeper_material(env):
    ids, _ = env
    r = _guest("guest-token").get(f"/api/modules/{ids['module']}")
    assert r.status_code == 200
    _assert_no_secrets(r.text)
    body = r.json()
    for key in ("npcs", "clues", "triggers", "handouts", "endings", "scenes", "map_nodes"):
        assert body[key] == [], key
    assert body["truth"] == ""


def test_local_host_module_detail_is_complete(env):
    ids, _ = env
    body = _host().get(f"/api/modules/{ids['module']}").json()
    assert body["truth"] == TRUTH
    assert body["npcs"][0]["secrets"] == NPC_SECRET
    assert body["endings"] and body["triggers"] and body["handouts"] and body["clues"]


# ── 2. 会话接口 ──────────────────────────────────────────────────────

def test_seated_guest_session_world_state_is_whitelisted(env):
    ids, _ = env
    r = _guest("guest-token").get(f"/api/sessions/{ids['ai']}")
    assert r.status_code == 200
    _assert_no_secrets(r.text)
    ws = r.json()["world_state"]
    # 前端唯一读取的键：结局按钮要它（名字可见，规划器理由不给）
    assert ws["ending_reached"] == {"id": "e1", "name": "火葬"}
    for key in ("backstage", "npc_memory", "clue_ledger", "improvised_npcs",
                "team_memory", "flags", "story_summary"):
        assert key not in ws, key


def test_guest_session_list_world_state_is_whitelisted(env):
    r = _guest("guest-token").get("/api/sessions")
    assert r.status_code == 200
    assert r.json(), "客人应能看到自己入座的局"
    _assert_no_secrets(r.text)


def test_player_in_human_kp_session_gets_whitelisted_world_state(env):
    ids, _ = env
    r = _guest("player-token").get(f"/api/sessions/{ids['kp']}")
    assert r.status_code == 200
    _assert_no_secrets(r.text)


def test_remote_human_kp_gets_full_world_state(env):
    ids, _ = env
    ws = _guest("kp-token").get(f"/api/sessions/{ids['kp']}").json()["world_state"]
    assert ws["backstage"]["note"] == BACKSTAGE_TEXT
    assert ws["npc_memory"]


def test_local_host_gets_full_world_state(env):
    ids, _ = env
    ws = _host().get(f"/api/sessions/{ids['ai']}").json()["world_state"]
    assert ws["backstage"]["note"] == BACKSTAGE_TEXT


def test_room_code_lookup_returns_only_join_info(env):
    ids, _ = env
    r = _guest("stranger").get(f"/api/sessions/by-code/{ids['ai_code']}")
    assert r.status_code == 200
    body = r.json()
    _assert_no_secrets(r.text)
    assert body["id"] == ids["ai"]
    assert body["module_title"] == "管家之谜"
    for key in ("world_state", "turn_state", "participants"):
        assert key not in body, key


# ── 3. 其余下发路径 ──────────────────────────────────────────────────

def _add_backstage_event(factory, session_id: str) -> None:
    db = factory()
    session_service.add_event(db, session_id, "narration", "雨下得更大了。", actor_name="KP")
    session_service.add_event(
        db, session_id, "system", BACKSTAGE_TEXT, actor_name="幕后",
        visibility=[session_service.KP_ONLY_SENTINEL], metadata={"kind": "backstage"},
    )
    db.close()


def test_events_and_search_skip_kp_only_events(env):
    ids, factory = env
    _add_backstage_event(factory, ids["ai"])
    guest = _guest("guest-token")
    events = guest.get(f"/api/sessions/{ids['ai']}/events")
    assert events.status_code == 200
    _assert_no_secrets(events.text)
    search = guest.get(f"/api/sessions/{ids['ai']}/search", params={"q": "地窖"})
    assert search.status_code == 200
    _assert_no_secrets(search.text)


def test_sync_snapshot_has_no_keeper_material(env):
    ids, _ = env
    r = _guest("guest-token").get(f"/api/sessions/{ids['ai']}/sync")
    assert r.status_code == 200
    _assert_no_secrets(r.text)


def test_improvised_npc_list_has_no_cards(env):
    ids, _ = env
    r = _guest("guest-token").get(f"/api/sessions/{ids['ai']}/improvised-npcs")
    assert r.status_code == 200
    _assert_no_secrets(r.text)


def test_recap_never_feeds_kp_only_events_to_the_writer(env, monkeypatch):
    """战报是玩家可见的：喂给撰稿模型的材料里不能有幕后推演。"""
    from app.services import recap_service

    ids, factory = env
    _add_backstage_event(factory, ids["ai"])
    seen: list[str] = []

    class _LLM:
        async def complete(self, messages, **_kw):
            seen.append("\n".join(m["content"] for m in messages))
            return '{"title": "雨夜", "key_decisions": [], "highlights": []}'

    monkeypatch.setattr(recap_service, "get_llm", lambda: _LLM())
    db = factory()
    # 滚动摘要本身就是从「已滤掉仅 KP 可见事件」的事件浓缩来的（generation_housekeeping），
    # 这里换成正常摘要，只验事件这一路。
    from app.models import GameSession
    gs = db.get(GameSession, ids["ai"])
    gs.world_state = {**gs.world_state, "story_summary": "调查员们抵达古宅。"}
    db.commit()
    entry = asyncio.run(recap_service.generate_and_store_recap(db, ids["ai"]))
    db.close()
    assert entry is not None
    assert seen and "雨下得更大了" in seen[0]
    assert BACKSTAGE_TEXT not in seen[0]


def test_replay_never_feeds_kp_only_events(env):
    from app.services import replay_service

    ids, factory = env
    _add_backstage_event(factory, ids["ai"])
    db = factory()
    events = replay_service._visible_story_events(db, ids["ai"])
    db.close()
    assert all(BACKSTAGE_TEXT not in (e.content or "") for e in events)


# ── 4. 真人 KP 的台词遮名与剧情标志 ──────────────────────────────────

def _human_kp_fixture(factory, ids):
    db = factory()
    from app.models import GameSession
    session = db.get(GameSession, ids["kp"])
    module = db.get(Module, ids["module"])
    return db, session, module


def test_human_kp_dialogue_masks_unmet_npc(env):
    """玩家还没认出管家：真人 KP 让他开口，气泡上不能印真名（与 AI KP 的 say 一致）。"""
    from app.services.chat_service import execute_human_kp_action

    ids, factory = env
    db, session, module = _human_kp_fixture(factory, ids)
    chunks, receipt = asyncio.run(execute_human_kp_action(
        db, session.id, session, module, "dialogue",
        {"npc_id": "butler", "content": "这么晚了，各位有何贵干？"},
    ))
    payload = chunks[0].as_wire()
    assert payload["actor_name"] != "管家"
    assert "管家" not in payload["actor_name"]
    db.close()


def test_set_flag_does_not_broadcast_flag_name(env):
    """标志名是模组作者写给 KP 的剧情结构（butler_exposed），不能当系统消息播给全桌。"""
    from app.services.chat_service import execute_human_kp_action

    ids, factory = env
    db, session, module = _human_kp_fixture(factory, ids)
    chunks, receipt = asyncio.run(execute_human_kp_action(
        db, session.id, session, module, "set_flag", {"flag": "butler_exposed"},
    ))
    for c in chunks:
        assert "butler_exposed" not in str(c.as_wire())
    assert "butler_exposed" in receipt      # KP 自己的回执照旧写明
    db.close()


# ── 5. 本机敏感接口拒绝外站网页 ────────────────────────────────────

def test_foreign_origin_cannot_read_local_api_key():
    """房主浏览器里打开的任意网页 fetch 回环地址，不能被当成房主本人。"""
    r = _host(None).get(
        "/api/settings/ai/profiles", headers={"Origin": "https://evil.example"},
    )
    assert r.status_code == 403


def test_dns_rebinding_host_is_rejected():
    """DNS 重绑定：同源 GET 不带 Origin，但 Host 是外站域名。"""
    r = _host(None).get(
        "/api/settings/ai/profiles", headers={"Host": "evil.example:8000"},
    )
    assert r.status_code == 403


@pytest.mark.parametrize("origin", [
    None,                              # 同源请求（打包版前端由后端同源托管）
    "http://127.0.0.1:8000",
    "http://localhost:5173",           # vite dev
    "tauri://localhost",               # 桌面壳加载页（macOS / Linux）
    "http://tauri.localhost",          # 桌面壳加载页（Windows）
])
def test_legit_local_origins_still_work(origin):
    headers = {"Origin": origin} if origin else {}
    r = _host(None).get("/api/settings/ai/profiles", headers=headers)
    assert r.status_code == 200, r.text


def test_foreign_origin_cannot_csrf_local_writes(env):
    """简单 POST 不触发预检：外站网页也不能借房主浏览器删模组。"""
    ids, _ = env
    r = _host(None).delete(
        f"/api/modules/{ids['module']}", headers={"Origin": "https://evil.example"},
    )
    assert r.status_code == 403
    assert _host(None).get(f"/api/modules/{ids['module']}").status_code == 200


def test_guest_origin_is_not_judged_by_local_rule(env):
    """客人的前端跑在他自己机器上（任意来源），来源校验只管本机请求，不误伤客人。"""
    ids, _ = env
    r = _guest("guest-token").get(
        f"/api/sessions/{ids['ai']}", headers={"Origin": "http://192.168.1.50:5173"},
    )
    assert r.status_code == 200


def test_guest_can_join_by_room_code(env):
    """凭房间码入房走的是两个限流端点；返回 dict 时 slowapi 要写限流头，缺 Response
    参数时客人一调就 500（此前只测过 404 / 429 分支，没照出来）。"""
    from app.models import GameSession

    ids, factory = env
    db = factory()
    db.get(GameSession, ids["ai"]).status = "setup"   # 回到大厅，留一个空真人席
    db.add(SessionParticipant(
        session_id=ids["ai"], role="human", seat_order=10, claimed=False, ready=False,
    ))
    db.commit()
    db.close()
    guest = _guest("new-guest")
    room = guest.get(f"/api/sessions/by-code/{ids['ai_code']}")
    assert room.status_code == 200, room.text
    joined = guest.post(f"/api/sessions/{room.json()['id']}/join")
    assert joined.status_code == 200, joined.text
    _assert_no_secrets(joined.text)


@pytest.mark.parametrize(("origin", "ok"), [
    ("http://127.0.0.1:8000", True),
    ("http://localhost:5173", True),
    ("http://[::1]:8000", True),
    ("http://tauri.localhost", True),
    ("https://tauri.localhost", True),
    ("tauri://localhost", True),
    ("https://evil.example", False),
    ("http://127.0.0.1.evil.example", False),
    ("http://192.168.1.20:8000", False),
    ("null", False),
    ("file://", False),
])
def test_is_app_origin(origin, ok):
    assert net_access.is_app_origin(origin) is ok


@pytest.mark.parametrize(("host", "rebindable"), [
    ("127.0.0.1:8000", False),
    ("localhost:5173", False),
    ("[::1]:8000", False),
    ("192.168.1.5:8000", False),
    ("testserver", False),
    ("my-mac", False),
    ("evil.example:8000", True),
    ("localhost.evil.example", True),
])
def test_is_rebindable_host(host, rebindable):
    assert net_access.is_rebindable_host(host) is rebindable
