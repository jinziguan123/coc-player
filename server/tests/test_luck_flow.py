"""幸运消费打断的那条结算链：投骰 → 停下问 → 拍板 → 接着走完。

这条链上挂着物品发货、线索记账和 KP 续写，全都以成败为输入。所以「要不要花幸运」
必须在骰子落地之后、这些结算之前问；一旦跑起来就回不了头了。
"""

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Character, GameSession, Module, SessionParticipant  # noqa: F401
from app.services import chat_service, session_service


@pytest.fixture
def db_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'luckflow.db'}", connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _patch_runtime(monkeypatch, db_factory):
    import app.database as database
    from app.services.room_hub import room_hub

    monkeypatch.setattr(database, "SessionLocal", db_factory)
    monkeypatch.setattr(chat_service, "get_llm", lambda: None)
    monkeypatch.setattr(chat_service, "get_fast_llm", lambda: None)
    monkeypatch.setattr(room_hub, "broadcast", lambda *a, **k: None)

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(chat_service, "_drain_housekeeping", _noop)
    # KP 续写是链条尾端唯一的 LLM 环节；记下它有没有被调到，就知道链条走没走完。
    calls: list[str] = []

    async def _fake_kp_turn(db, session_id, *a, **k):
        calls.append(session_id)

    monkeypatch.setattr(chat_service, "_run_kp_turn", _fake_kp_turn)
    return calls


def _seed(db, *, luck: int = 45, luck_spend: bool = True):
    module = Module(
        title="M", rule_system="coc", npcs=[],
        scenes=[{"id": "s1", "title": "书房", "name": "书房"}],
    )
    char = Character(
        name="陈守一", rule_system="coc", is_player=True,
        base_attributes={}, skills={"侦查": 60},
        system_data={"luck": luck, "sanity": {"current": 55, "max": 99}},
    )
    db.add_all([module, char]); db.flush()
    session = GameSession(
        module_id=module.id, player_character_id=char.id, status="active",
        world_state={}, current_scene_id="s1",
        rule_options={"luck_spend": True} if luck_spend else {},
    )
    db.add(session); db.flush()
    db.add(SessionParticipant(
        session_id=session.id, character_id=char.id, role="human",
        is_primary=True, seat_order=0, claimed=True, ready=True,
    ))
    db.commit()
    return session.id, char


def _hang_pending_check(db, session_id: str, char) -> str:
    check_id = "check-1"
    session_service.add_pending_check(db, session_id, {
        "id": check_id, "skill": "侦查", "difficulty": "normal",
        "char_ref": f"character:{char.id}", "char_id": char.id,
        "actor_name": char.name, "source": "", "reason": "抽屉合不严",
        "bonus": 0, "penalty": 0, "modifier_reason": "",
    })
    return check_id


def _roll(monkeypatch, value: int):
    from app.rules.coc import checks as checks_mod

    monkeypatch.setattr(checks_mod, "roll_percentile", lambda: value)


def test_near_miss_pauses_the_chain(db_factory, monkeypatch):
    """差 5 点 → 停下询价，KP 续写这时**绝不能**已经跑掉。"""
    calls = _patch_runtime(monkeypatch, db_factory)
    db = db_factory()
    session_id, char = _seed(db)
    check_id = _hang_pending_check(db, session_id, char)
    _roll(monkeypatch, 65)                      # 技能 60，差 5

    asyncio.run(chat_service.run_roll_generation(session_id, check_id))

    pending = session_service.get_pending_luck(db, session_id)
    assert pending is not None
    assert pending["offer"]["cost"] == 5
    assert calls == []                          # 链条停住了
    assert session_service.get_pending_check(db, session_id, check_id) is None  # 骰子已消费


def test_spending_luck_rewrites_the_verdict_and_resumes(db_factory, monkeypatch):
    calls = _patch_runtime(monkeypatch, db_factory)
    db = db_factory()
    session_id, char = _seed(db, luck=45)
    check_id = _hang_pending_check(db, session_id, char)
    _roll(monkeypatch, 65)
    asyncio.run(chat_service.run_roll_generation(session_id, check_id))

    asyncio.run(chat_service.run_luck_decision(session_id, True))

    db.expire_all()
    char = db.get(Character, char.id)
    assert char.system_data["luck"] == 40                       # 扣了 5 点
    assert session_service.get_pending_luck(db, session_id) is None
    assert calls == [session_id]                                # 链条接着走完了
    settled = [
        e for e in session_service.get_session_events(db, session_id)
        if (e.metadata_ or {}).get("luck_spend")
    ]
    assert len(settled) == 1
    assert settled[0].metadata_["roll_before"] == 65
    assert settled[0].metadata_["roll"] == 60
    assert settled[0].metadata_["outcome"] == "success"


def test_declining_keeps_the_failure(db_factory, monkeypatch):
    calls = _patch_runtime(monkeypatch, db_factory)
    db = db_factory()
    session_id, char = _seed(db, luck=45)
    check_id = _hang_pending_check(db, session_id, char)
    _roll(monkeypatch, 65)
    asyncio.run(chat_service.run_roll_generation(session_id, check_id))

    asyncio.run(chat_service.run_luck_decision(session_id, False))

    db.expire_all()
    assert db.get(Character, char.id).system_data["luck"] == 45   # 一点没花
    assert calls == [session_id]
    assert not [
        e for e in session_service.get_session_events(db, session_id)
        if (e.metadata_ or {}).get("luck_spend")
    ]


def test_rule_off_never_pauses(db_factory, monkeypatch):
    """家规没开幸运消费 → 一如既往地一路走到底，不多问一句。"""
    calls = _patch_runtime(monkeypatch, db_factory)
    db = db_factory()
    session_id, char = _seed(db, luck_spend=False)
    check_id = _hang_pending_check(db, session_id, char)
    _roll(monkeypatch, 65)

    asyncio.run(chat_service.run_roll_generation(session_id, check_id))

    assert session_service.get_pending_luck(db, session_id) is None
    assert calls == [session_id]


def test_second_decision_is_a_noop(db_factory, monkeypatch):
    """重复拍板不该再扣一次幸运——待决状态在第一次就被消费掉了。"""
    _patch_runtime(monkeypatch, db_factory)
    db = db_factory()
    session_id, char = _seed(db, luck=45)
    check_id = _hang_pending_check(db, session_id, char)
    _roll(monkeypatch, 65)
    asyncio.run(chat_service.run_roll_generation(session_id, check_id))
    asyncio.run(chat_service.run_luck_decision(session_id, True))
    asyncio.run(chat_service.run_luck_decision(session_id, True))

    db.expire_all()
    assert db.get(Character, char.id).system_data["luck"] == 40
