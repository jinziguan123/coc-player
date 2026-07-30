"""各人的角色库互不可见。

跑团的规则引擎在**房主机器上**跑：检定要读技能值、HP_CHANGE 要改血、物品与成长
都落在角色记录上，而 `SessionParticipant.character_id` 是指向房主本地 characters
表的外键。所以客人入座时必然要在房主机器上留一份可读写的**参战副本**——这是
「房主权威」架构的必然结果，不是实现偷懒。

但副本是**会话资产**，不该变成房主的藏品：此前房主打开「角色」页会看到一堆队友
的卡，还能删改它们。本文件盯住这条边界。
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
from app.services import net_access


@pytest.fixture(autouse=True)
def isolated_app(tmp_path, monkeypatch):
    """独立数据库 + 放行局域网来源。

    **数据库必须隔离**：直接用 app 会打到开发者本地的 server/trpg.db，既让用例
    依赖别人机器上的存量数据（换台机器就红），也会把测试角色写进真实库里。
    """
    engine = create_engine(
        f"sqlite:///{tmp_path / 'library.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    # 客人的请求要越过来源闸才能走到业务层
    monkeypatch.setattr(net_access, "SETTINGS_FILE", tmp_path / "net_settings.json")
    net_access.reset_cache()
    net_access.set_lan_enabled(True)
    yield
    app.dependency_overrides.clear()
    net_access.reset_cache()


def _client(token: str) -> TestClient:
    return TestClient(app, headers={"X-Player-Token": token})


def _create(token: str, name: str, is_player: bool = True) -> str:
    r = _client(token).post(
        "/api/characters",
        json={"name": name, "rule_system": "coc", "is_player": is_player},
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _names(token: str, **params) -> list[str]:
    r = _client(token).get("/api/characters", params=params)
    assert r.status_code == 200, r.text
    return [c["name"] for c in r.json()]


def test_others_cards_stay_out_of_your_library():
    _create("guest-tok", "客人的参战副本")
    _create("host-tok", "房主自己的卡")

    # 房主看不到客人留下的副本
    assert _names("host-tok") == ["房主自己的卡"]
    # 反过来同理：客人也看不到房主的藏品
    assert _names("guest-tok") == ["客人的参战副本"]


def test_unowned_cards_remain_visible_to_everyone():
    """无主的卡仍要可见。

    AI 队友（is_player=false，不绑 owner_token）与 identity 机制之前的旧数据都没有
    归属，一并过滤掉会让房主突然看不到自己以前建的角色。
    """
    _create("host-tok", "AI 队友", is_player=False)

    assert "AI 队友" in _names("host-tok")
    assert "AI 队友" in _names("stranger-tok")


def test_mine_filter_still_excludes_unowned():
    """`mine=true`（认领席位时用）比默认过滤更严：必须确实属于我。"""
    _create("host-tok", "我的卡")
    _create("host-tok", "无主卡", is_player=False)

    assert _names("host-tok", mine=True) == ["我的卡"]
