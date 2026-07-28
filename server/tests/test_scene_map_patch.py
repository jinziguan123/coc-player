"""沙盘单格拖拽落库：局内按 KP 席位授权，模组管理按本机授权。

这个端点原先既没有任何会话级校验、也没被前端调用过（P-Hex-2 留下、后被模组页的
整体保存取代）。P-Hex-4 让局内 KP 用它做拖拽修正，顺带把授权补齐。
"""

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
from app.services import net_access, session_service


@pytest.fixture
def db_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'sm.db'}", connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@pytest.fixture(autouse=True)
def _wired(db_factory, tmp_path, monkeypatch):
    """开放局域网，好让「客人 KP」的请求越过来源闸、走到真正的授权判断。"""
    monkeypatch.setattr(net_access, "SETTINGS_FILE", tmp_path / "net_settings.json")
    net_access.reset_cache()
    net_access.set_lan_enabled(True)

    def override():
        db = db_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    yield
    app.dependency_overrides.clear()
    net_access.reset_cache()


def _seed(db):
    module = Module(
        title="沙盘模组", rule_system="coc", npcs=[],
        scenes=[
            {"id": "s1", "name": "码头", "kind": "location", "map": {"q": 0, "r": 0, "biome": "coast"}},
            {"id": "s2", "name": "教堂", "kind": "location", "map": {"q": 2, "r": 0, "biome": "urban"}},
        ],
    )
    hero = Character(name="主角", rule_system="coc", is_player=True)
    db.add_all([module, hero])
    db.commit()
    # kp_mode="human" 时 create_session 会自动给创建者单开一个 KP 席
    session = session_service.create_session(
        db, module.id, [{"character_id": hero.id, "role": "human", "is_primary": True}],
        creator_token="kp-token", kp_mode="human",
    )
    session.status = "active"
    db.commit()
    return module, session


def _kp_token(db, session) -> str:
    """取真人 KP 席位的 token（建房时房主占 KP 席）。"""
    kp = next(p for p in session.participants if p.role == "kp")
    return kp.owner_token


def _patch(client, module_id, body, token=None):
    return client.patch(
        f"/api/modules/{module_id}/scene-map", json=body,
        headers={"X-Player-Token": token} if token else {},
    )


def test_in_session_kp_may_drag_even_from_lan(db_factory):
    """局内 KP 可能是连进来的客人——不能按「仅限本机」把关，否则远程 KP 根本拖不动。"""
    db = db_factory()
    module, session = _seed(db)
    guest = TestClient(app, client=("192.168.1.50", 1))

    r = _patch(guest, module.id,
               {"scene_id": "s2", "q": 3, "r": -1, "session_id": session.id},
               _kp_token(db, session))
    assert r.status_code == 200, r.text
    assert r.json()["map"]["q"] == 3 and r.json()["map"]["r"] == -1


def test_non_kp_player_may_not_drag(db_factory):
    """普通玩家席不能改地图——KP 席与玩家席权限严格分离。"""
    db = db_factory()
    module, session = _seed(db)
    guest = TestClient(app, client=("192.168.1.50", 1))

    r = _patch(guest, module.id,
               {"scene_id": "s2", "q": 3, "r": -1, "session_id": session.id},
               "not-the-kp-token")
    assert r.status_code in (403, 404)


def test_cannot_use_own_room_to_edit_another_module(db_factory):
    """拿一个自己是 KP 的房间去改别的模组，必须挡住。"""
    db = db_factory()
    module, session = _seed(db)
    other = Module(title="别人的模组", rule_system="coc", npcs=[],
                   scenes=[{"id": "x", "name": "别处", "kind": "location"}])
    db.add(other); db.commit()

    guest = TestClient(app, client=("192.168.1.50", 1))
    r = _patch(guest, other.id,
               {"scene_id": "x", "q": 1, "r": 1, "session_id": session.id},
               _kp_token(db, session))
    assert r.status_code == 403
    assert "不是这个模组" in r.json()["detail"]


def test_module_management_path_stays_local_only(db_factory):
    """不带 session_id = 模组管理语境，沿用 ADR-007 的仅限本机。"""
    db = db_factory()
    module, _ = _seed(db)

    guest = TestClient(app, client=("192.168.1.50", 1))
    assert _patch(guest, module.id, {"scene_id": "s2", "q": 4, "r": 0}).status_code == 403

    host = TestClient(app, client=("127.0.0.1", 1))
    assert _patch(host, module.id, {"scene_id": "s2", "q": 4, "r": 0}).status_code == 200


def test_collision_is_rejected(db_factory):
    """撞格必须 400，否则两个场景叠在同一格上。"""
    db = db_factory()
    module, session = _seed(db)
    host = TestClient(app, client=("127.0.0.1", 1))

    r = _patch(host, module.id, {"scene_id": "s2", "q": 0, "r": 0})   # s1 已占 (0,0)
    assert r.status_code == 400


def test_drag_is_idempotent(db_factory):
    """同一坐标重复拖拽结果一致——设计里点名要求的验收项。"""
    db = db_factory()
    module, session = _seed(db)
    host = TestClient(app, client=("127.0.0.1", 1))

    first = _patch(host, module.id, {"scene_id": "s2", "q": 5, "r": -2}).json()
    second = _patch(host, module.id, {"scene_id": "s2", "q": 5, "r": -2}).json()
    assert first == second


def test_in_session_drag_broadcasts_map_update(db_factory):
    """同时开着大地图的其他人要跟着更新，否则各自看到的位置不一致。"""
    from app.services.room_hub import room_hub

    db = db_factory()
    module, session = _seed(db)
    q = room_hub.subscribe(session.id)
    try:
        guest = TestClient(app, client=("192.168.1.50", 1))
        _patch(guest, module.id,
               {"scene_id": "s2", "q": 3, "r": -1, "session_id": session.id},
               _kp_token(db, session))
        events = []
        while not q.empty():
            events.append(q.get_nowait())
        assert any(e.type == "map_update" for e in events)
    finally:
        room_hub.unsubscribe(session.id, q)
