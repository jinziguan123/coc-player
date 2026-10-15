"""场外聊天通道：正文保留、长度校验、补漏分页、输入提示按席位、KP 上下文不泄露。"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.ai import context as ctx
from app.database import get_db
from app.main import app
from app.models import Base, Character, Module  # noqa: F401
from app.services import session_service
from app.services.event_protocol import MAX_OOC_CHARS, event_to_chunk, make_chunk, ooc_text
from app.services.room_hub import RoomHub


@pytest.fixture
def factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'chat.db'}", connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@pytest.fixture
def room(factory):
    """一桌两个真人席（房主有角色，客人没选角色），返回 (client, session_id, hero)。"""
    db = factory()
    module = Module(title="聊天模组", rule_system="coc", npcs=[], scenes=[])
    hero = Character(name="房主角色", rule_system="coc", is_player=True)
    db.add_all([module, hero])
    db.commit()
    session = session_service.create_session(
        db, module.id,
        [
            {"character_id": hero.id, "role": "human", "is_primary": True},
            {"character_id": None, "role": "human"},
        ],
        creator_token="host-token",
    )
    sid, hero_id = session.id, hero.id
    db.close()

    def override():
        d = factory()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_db] = override
    yield TestClient(app), sid, hero_id
    app.dependency_overrides.clear()


def _events(factory, sid):
    db = factory()
    try:
        return [(e.event_type, e.content) for e in session_service.get_session_events(db, sid)]
    finally:
        db.close()


def test_ooc_text_keeps_whole_sentence_unless_fully_parenthesized():
    assert ooc_text("我带医生（开玩笑）") == "我带医生（开玩笑）"
    assert ooc_text("  （等我一下）  ") == "等我一下"
    assert ooc_text("(brb)") == "brb"
    assert ooc_text("（）") == "（）"


def test_lobby_ooc_stores_full_text(room, factory):
    client, sid, _ = room
    r = client.post(f"/api/sessions/{sid}/ooc", json={"content": "我带医生（开玩笑）"},
                    headers={"X-Player-Token": "host-token"})
    assert r.status_code == 200, r.text
    assert ("ooc", "我带医生（开玩笑）") in _events(factory, sid)


@pytest.mark.parametrize("content", ["", "   ", "x" * (MAX_OOC_CHARS + 1)])
def test_ooc_rejects_empty_and_too_long(room, factory, content):
    client, sid, _ = room
    r = client.post(f"/api/sessions/{sid}/ooc", json={"content": content},
                    headers={"X-Player-Token": "host-token"})
    assert r.status_code == 400
    assert not [e for e in _events(factory, sid) if e[0] == "ooc"]


def test_chat_pure_ooc_is_rejected_without_side_effect(room, factory):
    client, sid, _ = room
    db = factory()
    session_service.get_session(db, sid).status = "active"
    db.commit()
    db.close()
    r = client.post(f"/api/sessions/{sid}/chat", json={"content": "（等我一下）"},
                    headers={"X-Player-Token": "host-token"})
    assert r.status_code == 400
    assert not [e for e in _events(factory, sid) if e[0] == "ooc"]


def test_events_after_seq_and_types(room, factory):
    client, sid, _ = room
    db = factory()
    for i in range(5):
        session_service.add_event(db, sid, "ooc", f"聊天{i}", actor_name="甲")
        session_service.add_event(db, sid, "narration", f"叙述{i}", actor_name="KP")
    db.close()
    h = {"X-Player-Token": "host-token"}

    latest = client.get(f"/api/sessions/{sid}/events?types=ooc&limit=2", headers=h).json()
    assert [e["content"] for e in latest["events"]] == ["聊天3", "聊天4"]
    assert latest["has_more"] is True

    first_seq = latest["events"][0]["sequence_num"]
    after = client.get(
        f"/api/sessions/{sid}/events?types=ooc&after_seq={first_seq}", headers=h,
    ).json()
    assert [e["content"] for e in after["events"]] == ["聊天4"]
    assert after["has_more"] is False

    page = client.get(f"/api/sessions/{sid}/events?types=ooc&after_seq=0&limit=2", headers=h).json()
    assert [e["content"] for e in page["events"]] == ["聊天0", "聊天1"]
    assert page["has_more"] is True


def test_typing_carries_seat_and_skips_inflight_buffer(room, factory, monkeypatch):
    client, sid, _ = room
    hub = RoomHub()
    monkeypatch.setattr("app.api.sessions.room_hub", hub)
    hub.begin_generation(sid)
    q = hub.subscribe(sid)
    # 客人入座：拿到 1 号席
    client.post(f"/api/sessions/{sid}/join", headers={"X-Player-Token": "guest-token"})
    while not q.empty():
        q.get_nowait()

    r = client.post(f"/api/sessions/{sid}/typing", headers={"X-Player-Token": "guest-token"})
    assert r.status_code == 200, r.text
    chunk = q.get_nowait()
    assert chunk.type == "typing"
    assert chunk.actor_name == "玩家"
    db = factory()
    guest_seat = session_service.ooc_seat_order(db, sid, "guest-token")
    host_seat = session_service.ooc_seat_order(db, sid, "host-token")
    db.close()
    assert chunk.metadata["seat"] == guest_seat != host_seat
    # 生成中途接入的人不该收到这条过期的「正在输入」
    assert all(c.type != "typing" for c in hub._inflight[sid])


def test_ephemeral_broadcast_not_replayed_to_late_subscriber():
    hub = RoomHub()
    hub.begin_generation("r")
    hub.broadcast("r", make_chunk("typing", actor_name="甲"), ephemeral=True)
    hub.broadcast("r", make_chunk("narration", "夜色"))
    q = hub.subscribe("r")
    got = [q.get_nowait().type for _ in range(q.qsize())]
    assert got == ["narration"]


def _ev(seq, etype, content, actor_id=None, actor_name=""):
    return SimpleNamespace(
        id=f"e{seq}", sequence_num=seq, event_type=etype, content=content, actor_id=actor_id,
        actor_name=actor_name, summary=None, metadata_={}, visibility=[],
    )


def test_messages_index_maps_merged_messages_to_event_starts():
    events = [
        _ev(1, "narration", "甲"), _ev(2, "ooc", "场外"), _ev(3, "narration", "乙"),
        _ev(4, "action", "推门", actor_id="h", actor_name="主角"),
        _ev(5, "system", "提示"), _ev(6, "narration", "丙"),
    ]
    msgs, starts = ctx._events_to_messages_indexed(events, primary_char_id="h")
    # 叙述甲+乙合并（中间的 ooc 被跳过），行动一条，丙一条
    assert [m["role"] for m in msgs] == ["assistant", "user", "assistant"]
    assert starts == [0, 3, 5]


def test_fallback_summary_excludes_ooc():
    events = [_ev(1, "narration", "雨夜的旅店"), _ev(2, "ooc", "我去倒杯水", actor_name="甲")]
    out = ctx._summarize_old_events(events, max_tokens=500)
    assert "雨夜的旅店" in out
    assert "倒杯水" not in out


def test_persisted_event_chunk_carries_seq():
    wire = event_to_chunk(_ev(42, "ooc", "在吗", actor_name="甲")).as_wire()
    assert wire["seq"] == 42
    assert "seq" not in make_chunk("typing", actor_name="甲").as_wire()
