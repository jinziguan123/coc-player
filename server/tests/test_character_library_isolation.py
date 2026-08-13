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


def _client(token: str | None) -> TestClient:
    """token=None 模拟「没带身份」的调用——这类调用建出来的卡就是无主卡。"""
    return TestClient(app, headers={"X-Player-Token": token} if token else {})


def _create(token: str | None, name: str, is_player: bool = True) -> str:
    r = _client(token).post(
        "/api/characters",
        json={"name": name, "rule_system": "coc", "is_player": is_player},
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _names(token: str | None, **params) -> list[str]:
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


def test_mine_filter_includes_unowned_player_cards():
    """`mine=true`（认领席位时用）= 我 token 名下的 + **无主的**。

    「无主」不等于「别人的」——它是「没人认领过归属」：identity 机制之前建的卡、
    没带 token 建的卡都是这一类。此前这里比默认过滤更严（要求 owner_token 非空），
    结果同一批卡「角色页看得见、进大厅却选不了」：用户建了一堆角色，待选却只有三个
    （实测库里 24 张卡有 12 张无主）。

    AI 队友不该出现在认领候选里——但那该由 `is_player=true` 挡，不该靠归属挡：
    大厅取真人席候选用的就是 `?available=true&is_player=true&mine=true`。
    """
    _create("host-tok", "我的卡")
    _create(None, "早期建的无主卡")
    _create("other-tok", "别人的卡")

    got = _names("host-tok", mine=True)
    assert "我的卡" in got and "早期建的无主卡" in got
    assert "别人的卡" not in got            # 别人的仍然不给


def test_mine_filter_still_excludes_ai_teammates_by_is_player():
    """AI 队友靠 is_player 过滤挡在真人席候选之外，与归属无关。"""
    _create("host-tok", "我的卡")
    _create("host-tok", "AI 队友卡", is_player=False)

    assert _names("host-tok", mine=True, is_player=True) == ["我的卡"]


def test_真人认领队友卡时转正为玩家角色():
    """AI 队友卡被真人坐上去，就该变成一张玩家调查员卡。

    is_player 决定的只是归档口径（结局后只给玩家角色写模组经历）与它出现在哪个
    候选池里；谁驱动这个角色看的是席位的 role。不转正的话，玩家认领完一张队友卡，
    下次在「我的角色」里再也找不到它——卡还是那张卡，只因为当初点的是哪个按钮。
    """
    from app.models import Character as Char

    client = _client("host-tok")
    ally_id = _create(None, "AI 生成的队友", is_player=False)
    mod = client.post("/api/modules", json={
        "title": "认领测试模组", "rule_system": "coc", "description": "",
    })
    assert mod.status_code == 200, mod.text
    room = client.post("/api/sessions", json={
        "module_id": mod.json()["id"],
        "participants": [{"character_id": None, "role": "human", "is_primary": True}],
    })
    assert room.status_code == 200, room.text

    claimed = client.post(
        f"/api/sessions/{room.json()['id']}/claim",
        json={"seat_order": 0, "character_id": ally_id},
    )
    assert claimed.status_code == 200, claimed.text

    # 转正后它才会回到「我的角色」候选里
    assert "AI 生成的队友" in _names("host-tok", mine=True, is_player=True)

    db = next(iter(app.dependency_overrides[get_db]()))
    try:
        assert db.get(Char, ally_id).is_player is True
    finally:
        db.close()
