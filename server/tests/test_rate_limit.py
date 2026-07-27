"""速率限制与房间码强度：ADR-007 里「房间码可枚举、加入无限流」那条未决项。"""

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
from app.services import net_access, rate_limit, session_service


@pytest.fixture
def db_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'rl.db'}", connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@pytest.fixture(autouse=True)
def _fresh_limiter(tmp_path, monkeypatch, db_factory):
    """每个用例清空限流计数并开放局域网，否则用例之间会互相污染。"""
    monkeypatch.setattr(net_access, "SETTINGS_FILE", tmp_path / "net_settings.json")
    net_access.reset_cache()
    net_access.set_lan_enabled(True)

    rate_limit.limiter.reset()

    def override():
        db = db_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    yield
    app.dependency_overrides.clear()
    rate_limit.limiter.reset()
    net_access.reset_cache()


def _guest(ip: str = "192.168.1.50") -> TestClient:
    return TestClient(app, client=(ip, 5555))


def _limit_count(limit: str) -> int:
    return int(limit.split("/")[0])


def test_room_code_lookup_is_rate_limited():
    """房间码枚举：猜错也要计数，否则限流形同虚设。"""
    guest = _guest()
    allowed = _limit_count(rate_limit.ROOM_CODE_LOOKUP_LIMIT)

    codes = [f"AAAAAA{i:02d}" for i in range(allowed + 5)]
    statuses = [guest.get(f"/api/sessions/by-code/{c}").status_code for c in codes]

    assert statuses[:allowed] == [404] * allowed      # 猜错 → 404，但已计数
    assert 429 in statuses[allowed:], "超出配额后应当被限流"


def test_rate_limit_is_per_source_address():
    """限流按来源计数：一个客人刷爆了，不应连累另一个客人。"""
    noisy = _guest("192.168.1.50")
    for i in range(_limit_count(rate_limit.ROOM_CODE_LOOKUP_LIMIT) + 3):
        noisy.get(f"/api/sessions/by-code/BBBBBB{i:02d}")
    assert noisy.get("/api/sessions/by-code/BBBBBBZZ").status_code == 429

    quiet = _guest("192.168.1.77")
    assert quiet.get("/api/sessions/by-code/CCCCCCCC").status_code == 404


def test_host_local_requests_are_exempt():
    """房主在自己机器上不该被限——桌面版前后端同源，正常使用就会有突发请求。"""
    host = TestClient(app, client=("127.0.0.1", 1))
    over = _limit_count(rate_limit.ROOM_CODE_LOOKUP_LIMIT) + 10
    statuses = {host.get(f"/api/sessions/by-code/DDDDDD{i:02d}").status_code for i in range(over)}
    assert statuses == {404}


def test_join_is_rate_limited(db_factory):
    guest = _guest("192.168.1.60")
    allowed = _limit_count(rate_limit.JOIN_LIMIT)
    statuses = [
        guest.post(f"/api/sessions/nonexistent-{i}/join").status_code
        for i in range(allowed + 3)
    ]
    assert 429 in statuses[allowed:]


# ── 房间码强度 ────────────────────────────────────────────────────────────


def test_room_code_alphabet_and_length(db_factory):
    """8 位、32 字符表 ≈ 40 bit；旧版是 6 位 hex ≈ 24 bit，可在线枚举。"""
    db = db_factory()
    module = Module(title="m", rule_system="coc", npcs=[], scenes=[])
    db.add(module); db.commit()

    codes = set()
    for i in range(20):
        c = Character(name=f"角色{i}", rule_system="coc", is_player=True)
        db.add(c); db.commit()
        s = session_service.create_session(
            db, module.id,
            [{"character_id": c.id, "role": "human", "is_primary": True}],
            creator_token="tok",
        )
        codes.add(s.room_code)

    assert len(codes) == 20, "房间码应当互不相同"
    for code in codes:
        assert len(code) == 8
        # 去掉了易混的 I/O/0/1
        assert set(code) <= set(session_service._ROOM_CODE_ALPHABET)
        assert not (set(code) & set("IO01"))


def test_limiter_counts_per_endpoint_not_per_url():
    """slowapi 默认 key_style="url"，而房间码在路径里——那样每猜一个码都是独立计数桶，
    防枚举完全失效。这条盯住配置本身，避免升级或重构时被悄悄改回默认值。"""
    assert rate_limit.limiter._key_style == "endpoint"
