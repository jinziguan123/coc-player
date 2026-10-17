"""回合确认的掉线重判：已确认者都在线、未确认者掉线时，断线那一刻就推进。

从前只在 /advance 时判定：A 已确认、B 随后掉线，A 的「推进」按钮已禁用，没人能触发推进。
"""

from __future__ import annotations

import pytest

from app import database
from app.api import chat
from app.models import GameSession
from app.services import session_service
from app.services.generation_manager import generation_manager
from app.services.room_hub import room_hub

from tests.test_multiplayer_robustness import _Online, _two_player_room, env  # noqa: F401


@pytest.fixture
def started(env, monkeypatch):  # noqa: F811
    """让断线回调用测试库，并把生成启动换成记录（不真跑 AI）。"""
    monkeypatch.setattr(database, "SessionLocal", env)
    calls: list[str] = []

    def fake_start(room_id, coro, prelude=None):
        coro.close()
        calls.append(room_id)

    monkeypatch.setattr(generation_manager, "start", fake_start)
    return calls


def _confirms(factory, sid: str) -> dict:
    db = factory()
    try:
        return session_service.live_turn_state(db, sid)
    finally:
        db.close()


def test_leave_listener_is_registered():
    assert chat._reevaluate_turn_on_leave in room_hub._leave_listeners


def test_holdout_disconnect_advances_turn(env, started):  # noqa: F811
    ids = _two_player_room(env)
    db = env()
    session_service.set_turn_confirm(db, ids["sid"], ids["hero"], True)
    db.close()

    online = _Online(ids["sid"], "host")  # 客人已掉线
    try:
        chat._reevaluate_turn_on_leave(ids["sid"])
        assert started == [ids["sid"]]
        # 推进后确认状态清空
        assert _confirms(env, ids["sid"])["confirmed_ids"] == []
    finally:
        online.close()


def test_no_confirmation_does_not_advance(env, started):  # noqa: F811
    ids = _two_player_room(env)
    online = _Online(ids["sid"], "host")
    try:
        chat._reevaluate_turn_on_leave(ids["sid"])
    finally:
        online.close()
    assert started == []


def test_holdout_still_online_does_not_advance(env, started):  # noqa: F811
    ids = _two_player_room(env)
    db = env()
    session_service.set_turn_confirm(db, ids["sid"], ids["hero"], True)
    db.close()
    online = _Online(ids["sid"], "host", "guest")
    try:
        chat._reevaluate_turn_on_leave(ids["sid"])
    finally:
        online.close()
    assert started == []


def test_generating_does_not_advance(env, started, monkeypatch):  # noqa: F811
    ids = _two_player_room(env)
    db = env()
    session_service.set_turn_confirm(db, ids["sid"], ids["hero"], True)
    db.close()
    monkeypatch.setattr(generation_manager, "is_generating", lambda room_id: True)
    online = _Online(ids["sid"], "host")
    try:
        chat._reevaluate_turn_on_leave(ids["sid"])
        assert started == []
        assert _confirms(env, ids["sid"])["confirmed_ids"] == [ids["hero"]]
    finally:
        online.close()


def test_inactive_session_does_not_advance(env, started):  # noqa: F811
    ids = _two_player_room(env, status="ended")
    db = env()
    session_service.set_turn_confirm(db, ids["sid"], ids["hero"], True)
    db.close()
    online = _Online(ids["sid"], "host")
    try:
        chat._reevaluate_turn_on_leave(ids["sid"])
    finally:
        online.close()
    assert started == []


def test_human_kp_mode_hands_turn_to_kp(env, started, monkeypatch):  # noqa: F811
    ids = _two_player_room(env, kp_mode="human")
    db = env()
    session_service.set_turn_confirm(db, ids["sid"], ids["hero"], True)
    db.close()
    handed: list[str] = []
    monkeypatch.setattr(
        chat, "_continue_human_kp_turn",
        lambda db, sid, gs: handed.append(sid),
    )
    online = _Online(ids["sid"], "host")
    try:
        chat._reevaluate_turn_on_leave(ids["sid"])
    finally:
        online.close()
    assert handed == [ids["sid"]]
    assert started == []
    db = env()
    assert db.get(GameSession, ids["sid"]).status == "active"
    db.close()
