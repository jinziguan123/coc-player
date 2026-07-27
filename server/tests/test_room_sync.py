"""房间状态快照 /sync：断线重连对齐 sync 类状态的唯一出口。"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import get_db
from app.main import app
from app.models import (  # noqa: F401 — 注册全部表
    Base,
    Character,
    EventLog,
    GameSession,
    Module,
    SessionParticipant,
)
from app.services import combat_service, room_sync, session_service


@pytest.fixture
def db_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'sync.db'}", connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@pytest.fixture
def client(db_factory):
    def override():
        db = db_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _seed(db):
    module = Module(title="模组", rule_system="coc", npcs=[], scenes=[])
    hero = Character(name="主角", rule_system="coc", is_player=True)
    db.add_all([module, hero])
    db.commit()
    session = session_service.create_session(
        db, module.id, [{"character_id": hero.id, "role": "human", "is_primary": True}],
        creator_token="tok",
    )
    session.status = "active"
    db.commit()
    return session, hero


def _get(client, session_id, params=""):
    return client.get(
        f"/api/sessions/{session_id}/sync{params}", headers={"X-Player-Token": "tok"},
    )


def test_sync_returns_all_systems_and_watermark(client, db_factory):
    db = db_factory()
    session, _ = _seed(db)

    body = _get(client, session.id).json()
    assert set(body) == {"seq", "generating", "systems"}
    # 三个系统都要在——turn 此前根本没有查询端点，重连时确认进度只能靠错过的那条广播
    assert set(body["systems"]) == {"combat", "chase", "turn"}
    assert body["systems"]["combat"] == {"active": False}
    assert body["systems"]["chase"] == {"active": False}
    assert body["generating"] is False
    assert body["seq"] == 0  # 尚无事件


def test_watermark_follows_events(client, db_factory):
    db = db_factory()
    session, _ = _seed(db)

    session_service.add_event(db, session.id, "action", "我搜查抽屉", actor_name="主角")
    first = _get(client, session.id).json()["seq"]
    assert first > 0

    session_service.add_event(db, session.id, "narration", "抽屉是空的。", actor_name="KP")
    assert _get(client, session.id).json()["seq"] > first


def test_watermark_counts_kp_only_events(client, db_factory):
    """水位线是传输层序号，与可见性无关。

    若复用会过滤「仅 KP 可见」事件的取页函数，最后一条恰好是幕后推演时水位线会被
    错报成 0，客户端便会把已经应用过的缓冲事件再走一遍。
    """
    db = db_factory()
    session, _ = _seed(db)
    session_service.add_event(db, session.id, "action", "看得见的", actor_name="主角")
    visible_seq = room_sync.current_seq(db, session.id)

    session_service.add_event(
        db, session.id, "system", "幕后推演", actor_name="系统",
        metadata={"visibility": ["kp"]},
    )
    assert room_sync.current_seq(db, session.id) > visible_seq


def test_active_combat_appears_in_snapshot(client, db_factory):
    """战斗中断线重连：快照要能把 HUD 恢复出来。"""
    db = db_factory()
    session, hero = _seed(db)
    combat_service.start_combat(
        db, session.id,
        [{"id": hero.id, "name": hero.name, "side": "player", "is_human": True,
          "hp": 12, "max_hp": 12, "status": "active", "dex": 60}],
        [{"id": "e1", "name": "打手", "side": "enemy", "is_human": False,
          "hp": 10, "max_hp": 10, "status": "active", "dex": 40}],
        trigger="遭遇",
    )

    combat = _get(client, session.id).json()["systems"]["combat"]
    assert combat["active"] is True
    assert any(c["name"] == "打手" for c in combat["order"])


def test_systems_param_limits_scope_and_ignores_unknown(client, db_factory):
    """未知系统名忽略而非 400：前端可以先请求新系统，旧后端不会因此报错。"""
    db = db_factory()
    session, _ = _seed(db)

    body = _get(client, session.id, "?systems=turn").json()
    assert set(body["systems"]) == {"turn"}

    body = _get(client, session.id, "?systems=turn,还没做的系统").json()
    assert set(body["systems"]) == {"turn"}


def test_sync_requires_viewer_permission(client, db_factory):
    db = db_factory()
    session, _ = _seed(db)
    r = client.get(
        f"/api/sessions/{session.id}/sync", headers={"X-Player-Token": "someone-else"},
    )
    assert r.status_code == 403
