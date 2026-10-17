"""追逐状态机（P5）单测：起追逐 / 逐轮推 gap / 脱身&被追上折回。掷骰钉死，agent=None。"""

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Character, GameSession, Module
from app.services import chase_service


@pytest.fixture
def db_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path/'ch.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seq(values):
    it = iter(values)
    return lambda: next(it)


def _seed(db):
    module = Module(title="M", rule_system="coc", npcs=[], scenes=[])
    hero = Character(name="伊芙琳", rule_system="coc", is_player=True,
                     base_attributes={"DEX": 60}, skills={"运动": 60},
                     system_data={"move": 8})
    db.add_all([module, hero]); db.flush()
    s = GameSession(module_id=module.id, player_character_id=hero.id, status="active", world_state={})
    db.add(s); db.commit()
    return s.id, hero


def _start(db, sid, hero, **kw):
    q = chase_service._quarry_from_char(hero)
    p = chase_service._pursuer_from_npc({"name": "追兵", "attributes": {"DEX": 55},
                                         "skills": {"运动": 55}, "mov": 8})
    return chase_service.start_chase(db, sid, q, p, **kw)


def test_quarry_wins_round_opens_gap(db_factory, monkeypatch):
    db = db_factory()
    sid, hero = _seed(db)
    _start(db, sid, hero, escape_at=5, caught_at=-3)
    monkeypatch.setattr("app.rules.coc.components.check.roll_percentile", _seq([45, 80]))  # 逃成功、追失败
    asyncio.run(chase_service.resolve_chase_round(db, sid, {"type": "run"}))
    st = chase_service.get_chase(db.get(GameSession, sid))
    assert st["gap"] == 1 and st["round"] == 1


def test_group_chase_keeps_all_names_and_uses_slowest_fastest(db_factory):
    db = db_factory()
    sid, hero = _seed(db)
    other = Character(name="记者", rule_system="coc", is_player=True,
                      base_attributes={"DEX": 50}, skills={"运动": 55},
                      system_data={"move": 6})
    db.add(other); db.commit()

    q1 = chase_service._quarry_from_char(hero)
    q2 = chase_service._quarry_from_char(other)
    p1 = chase_service._pursuer_from_npc({"name": "慢追兵", "attributes": {"DEX": 45},
                                          "skills": {"运动": 45}, "mov": 7})
    p2 = chase_service._pursuer_from_npc({"name": "快追兵", "attributes": {"DEX": 70},
                                          "skills": {"运动": 65}, "mov": 9})
    _state, chunks = chase_service.start_chase(
        db, sid, q1, p1, quarries=[q1, q2], pursuers=[p1, p2],
    )
    st = chase_service.get_chase(db.get(GameSession, sid))
    assert [item["name"] for item in st["quarries"]] == ["伊芙琳", "记者"]
    assert [item["name"] for item in st["pursuers"]] == ["慢追兵", "快追兵"]
    # 慢逃者（记者 MOV 6）与快追兵（MOV 9）代表全组进距离轨
    assert st["quarry"]["name"] == "记者"
    assert st["pursuer"]["name"] == "快追兵"
    wire = next(c.as_wire() for c in chunks if c.as_wire().get("type") == "chase_start")
    assert wire["metadata"]["quarries"] == ["伊芙琳", "记者"]
    assert wire["metadata"]["pursuers"] == ["慢追兵", "快追兵"]


def test_escape_folds_result(db_factory, monkeypatch):
    db = db_factory()
    sid, hero = _seed(db)
    _start(db, sid, hero, escape_at=2, caught_at=-3)     # 阈值调小，易脱身
    # 连续两轮逃方大成功（+2）→ gap 达 2 → 脱身
    monkeypatch.setattr("app.rules.coc.components.check.roll_percentile", _seq([10, 80]))
    chunks = asyncio.run(chase_service.resolve_chase_round(db, sid, {"type": "run"}))
    session = db.get(GameSession, sid)
    assert chase_service.get_chase(session) is None                       # 追逐态已清
    assert session.world_state.get("combat_result", {}).get("outcome") == "escaped"
    assert any(c.type == "chase_end" for c in chunks)


def test_caught_folds_result(db_factory, monkeypatch):
    db = db_factory()
    sid, hero = _seed(db)
    _start(db, sid, hero, escape_at=5, caught_at=-1)     # 一轮被追上
    monkeypatch.setattr("app.rules.coc.components.check.roll_percentile", _seq([80, 10]))  # 逃失败、追大成功(-2)
    asyncio.run(chase_service.resolve_chase_round(db, sid, {"type": "run"}))
    session = db.get(GameSession, sid)
    assert chase_service.get_chase(session) is None
    assert session.world_state.get("combat_result", {}).get("outcome") == "caught"
