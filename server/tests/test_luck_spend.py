"""幸运消费（L1b）：花 1 点幸运降 1 点骰值，把失败买成成功。

这是「放水」的正规出口——KP 暗中降难度一旦被察觉，骰子系统就不可信了；幸运花的是玩家
自己的资源、由玩家自己拍板，每一点都记在卡上。CoC 7e 原文的边界逐条在此把关。
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Character, GameSession, Module, SessionParticipant  # noqa: F401
from app.rules.coc import luck as coc_luck
from app.rules.coc import options as coc_options
from app.rules.coc.checks import resolve_skill_check
from app.services import session_service

ON = coc_options.from_dict({"luck_spend": True})


@pytest.fixture
def db_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'luck.db'}", connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _rolled(roll: int, skill_value: int = 60, difficulty: str = "normal", monkeypatch=None):
    from app.rules.coc import checks as checks_mod

    monkeypatch.setattr(checks_mod, "roll_percentile", lambda: roll)
    return resolve_skill_check({"skills": {"侦查": skill_value}}, "侦查", difficulty)


def _char(luck: int, *, in_attrs: bool = False) -> dict:
    if in_attrs:
        return {"base_attributes": {"LUCK": luck}, "skills": {}, "system_data": {}}
    return {"base_attributes": {}, "skills": {}, "system_data": {"luck": luck}}


# ── 什么时候可以买 ────────────────────────────────────────────────────────


def test_offer_covers_exactly_the_gap(monkeypatch):
    """差几点就报几点，一点不多花。"""
    result = _rolled(65, 60, monkeypatch=monkeypatch)   # 目标 60，差 5
    offer = coc_luck.rescue_offer(result, "normal", _char(40), ON)
    assert offer == {"cost": 5, "available": 40, "target": 60}


def test_no_offer_when_rule_disabled(monkeypatch):
    result = _rolled(65, 60, monkeypatch=monkeypatch)
    assert coc_luck.rescue_offer(result, "normal", _char(40)) is None


def test_no_offer_when_luck_is_short(monkeypatch):
    result = _rolled(90, 60, monkeypatch=monkeypatch)   # 差 30
    assert coc_luck.rescue_offer(result, "normal", _char(10), ON) is None


def test_fumble_cannot_be_bought_back(monkeypatch):
    """原文：大失败不可挽回——它是掷出来的定局，不是差几点的问题。"""
    result = _rolled(100, 60, monkeypatch=monkeypatch)
    assert result.outcome == "fumble"
    assert coc_luck.rescue_offer(result, "normal", _char(99), ON) is None


def test_success_needs_no_rescue(monkeypatch):
    result = _rolled(30, 60, monkeypatch=monkeypatch)
    assert coc_luck.rescue_offer(result, "normal", _char(99), ON) is None


def test_san_and_luck_checks_are_forbidden(monkeypatch):
    from app.rules.coc import checks as checks_mod

    monkeypatch.setattr(checks_mod, "roll_percentile", lambda: 65)
    for skill in ("SAN", "理智", "幸运"):
        result = resolve_skill_check({"skills": {skill: 60}}, skill)
        assert coc_luck.rescue_offer(result, "normal", _char(99), ON) is None, skill


def test_house_rules_can_cap_and_ban_in_combat(monkeypatch):
    result = _rolled(65, 60, monkeypatch=monkeypatch)   # 差 5
    capped = coc_options.from_dict({"luck_spend": True, "luck_spend_max": 3})
    assert coc_luck.rescue_offer(result, "normal", _char(99), capped) is None
    no_combat = coc_options.from_dict(
        {"luck_spend": True, "luck_spend_in_combat": False},
    )
    assert coc_luck.rescue_offer(
        result, "normal", _char(99), no_combat, in_combat=True,
    ) is None
    assert coc_luck.rescue_offer(
        result, "normal", _char(99), no_combat, in_combat=False,
    ) is not None


def test_pushed_roll_cannot_be_rescued(monkeypatch):
    result = _rolled(65, 60, monkeypatch=monkeypatch)
    assert coc_luck.rescue_offer(result, "normal", _char(99), ON, pushed=True) is None


# ── 买下之后 ──────────────────────────────────────────────────────────────


def test_apply_rescue_reruns_the_same_judge(monkeypatch):
    """降完骰值走同一套判据——买来的成功必须由掷出来的那段代码认定。"""
    result = _rolled(65, 60, monkeypatch=monkeypatch)
    assert result.outcome == "failure"
    rescued = coc_luck.apply_rescue(result, "normal", 5, ON)
    assert rescued.roll == 60
    assert rescued.outcome == "success"
    assert rescued.meets_difficulty is True
    assert result.roll == 65 and result.outcome == "failure"   # 原件不动


def test_rescue_can_reach_a_higher_tier(monkeypatch):
    """花得多可以买到更高的成功等级（原文允许，一路买到大成功也行）。"""
    result = _rolled(65, 60, monkeypatch=monkeypatch)
    assert coc_luck.apply_rescue(result, "normal", 35, ON).tier == "hard"     # 30 ≤ 60//2
    assert coc_luck.apply_rescue(result, "normal", 53, ON).tier == "extreme"  # 12 ≤ 60//5


def test_deduct_handles_both_storage_shapes():
    assert coc_luck.deduct(_char(40), 5) == {
        "path": "system_data.luck", "old": 40, "new": 35,
    }
    assert coc_luck.deduct(_char(40, in_attrs=True), 5) == {
        "path": "base_attributes.LUCK", "old": 40, "new": 35,
    }


def test_available_luck_reads_both_shapes():
    assert coc_luck.available_luck(_char(42)) == 42
    assert coc_luck.available_luck(_char(42, in_attrs=True)) == 42


# ── 待决状态 ──────────────────────────────────────────────────────────────


def test_pending_luck_is_single_slot(db_factory):
    """同一时刻至多一份待决：这个断点会把整条结算链停住。"""
    db = db_factory()
    module = Module(title="M", rule_system="coc", npcs=[], scenes=[])
    db.add(module); db.flush()
    session = GameSession(module_id=module.id, status="active", world_state={})
    db.add(session); db.commit()

    session_service.set_pending_luck(db, session.id, {"cost": 5})
    assert session_service.get_pending_luck(db, session.id) == {"cost": 5}
    session_service.set_pending_luck(db, session.id, {"cost": 3})
    assert session_service.get_pending_luck(db, session.id) == {"cost": 3}
    session_service.set_pending_luck(db, session.id, None)
    assert session_service.get_pending_luck(db, session.id) is None


def test_luck_bought_success_earns_no_improvement_tick(db_factory):
    """照规则书：花幸运买来的成功不给成长勾——走运没教会你任何事。

    判据不能只看成败：那次结算已经把骰子事件的 outcome 改写成成功了。
    """
    from app.services import growth_service

    db = db_factory()
    module = Module(title="M", rule_system="coc", npcs=[], scenes=[])
    char = Character(
        name="陈守一", rule_system="coc", is_player=True,
        base_attributes={}, skills={"侦查": 60, "聆听": 55}, system_data={"luck": 40},
    )
    db.add_all([module, char]); db.flush()
    session = GameSession(
        module_id=module.id, player_character_id=char.id, status="active",
        world_state={}, rule_options={"luck_spend": True},
    )
    db.add(session); db.commit()

    # 一次自己掷出来的成功，一次花幸运买来的成功
    session_service.add_event(
        db, session.id, "dice", "聆听成功", actor_name="系统",
        metadata={"skill": "聆听", "actor": char.name, "outcome": "success"},
    )
    session_service.add_event(
        db, session.id, "dice", "侦查成功", actor_name="系统",
        metadata={"skill": "侦查", "actor": char.name, "outcome": "success", "luck_spent": 5},
    )

    eligible = {item["skill"] for item in growth_service.eligible_skills(db, session.id, char.id)}
    assert eligible == {"聆听"}

    # 村规把这条关掉 → 买来的成功照样给成长机会
    session.rule_options = {"luck_spend": True, "luck_spend_blocks_improvement": False}
    db.commit()
    relaxed = {item["skill"] for item in growth_service.eligible_skills(db, session.id, char.id)}
    assert relaxed == {"聆听", "侦查"}
