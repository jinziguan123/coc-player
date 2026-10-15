"""规则漂移回归：叙事扣血与战斗共用重伤口径、战斗/追逐检定读本局村规、
KP 提示词的疯狂口径与代码默认一致、状态严重度表只有一份。"""

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.ai.prompts.kp_system import KP_SYSTEM_PROMPT
from app.models import Base, Character, GameSession, Module  # noqa: F401
from app.rules.coc import chase, combat
from app.rules.coc import options as coc_options
from app.services import chat_service, combat_service, rule_options_service, session_service, turn_effects
from tests.wire import wires


@pytest.fixture
def db_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'drift.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seq(values):
    it = iter(values)
    return lambda: next(it)


def _seed(db, max_hp=12):
    module = Module(title="M", rule_system="coc", npcs=[], scenes=[])
    hero = Character(
        name="亨利", rule_system="coc", is_player=True,
        base_attributes={"DEX": 70, "CON": 60, "SIZ": 50},
        skills={"格斗(斗殴)": 60, "闪避": 35},
        system_data={"hitPoints": {"current": max_hp, "max": max_hp}, "damageBonus": "0"},
        status="active",
    )
    db.add_all([module, hero]); db.flush()
    s = GameSession(module_id=module.id, player_character_id=hero.id, status="active", world_state={})
    db.add(s); db.commit()
    return module, hero, s.id


# ── 1. 叙事扣血走与战斗相同的重伤口径（含村规 major_wound_divisor）──────────


def _narrative_damage(db, module, char, sid, delta):
    return wires(asyncio.run(chat_service._exec_hp_change(
        db, sid, char, "player", delta, "测试伤害", module=module,
    )))


def _major_wound_checks(db, sid):
    return [e for e in session_service.get_session_events(db, sid)
            if e.event_type == "dice" and (e.metadata_ or {}).get("major_wound_check")]


def test_narrative_damage_raw_half_threshold(db_factory, monkeypatch):
    """RAW：最大 HP 12，单击 4 点 < 半血 6 → 不算重伤。"""
    db = db_factory(); module, hero, sid = _seed(db)
    monkeypatch.setattr("app.rules.coc.checks.roll_percentile", lambda: 30)
    _narrative_damage(db, module, hero, sid, "-4")
    assert hero.status == "active"
    assert _major_wound_checks(db, sid) == []


def test_narrative_damage_respects_major_wound_divisor(db_factory, monkeypatch):
    """村规重伤线 1/3：最大 HP 12，单击 4 点 ≥ 4 → 叙事扣血也判重伤并过体质检定。"""
    db = db_factory(); module, hero, sid = _seed(db)
    rule_options_service.save_village_options(db, "coc", {"major_wound_divisor": 3})
    monkeypatch.setattr("app.rules.coc.checks.roll_percentile", lambda: 30)   # 体质 60 成功
    _narrative_damage(db, module, hero, sid, "-4")
    assert hero.status == "major_wound"
    assert len(_major_wound_checks(db, sid)) == 1


def test_is_major_wound_shared_threshold():
    thirds = coc_options.from_dict({"major_wound_divisor": 3})
    assert combat.is_major_wound(6, 12) is True
    assert combat.is_major_wound(5, 12) is False
    assert combat.is_major_wound(4, 12, thirds) is True


# ── 2. 战斗 / 追逐内部检定读本局村规 ──────────────────────────────────────


_ATK = {"skills": {"格斗(斗殴)": 50}, "base_attributes": {}}
_CRIT5 = coc_options.from_dict({"critical_max": 5})


def test_combat_attack_check_uses_critical_max(monkeypatch):
    monkeypatch.setattr("app.rules.coc.checks.roll_percentile", lambda: 3)
    assert combat.resolve_attack(_ATK, "0", "徒手格斗")["attacker_check"].outcome != "critical_success"
    r = combat.resolve_attack(_ATK, "0", "徒手格斗", options=_CRIT5)
    assert r["attacker_check"].outcome == "critical_success"


def test_combat_other_checks_take_options(monkeypatch):
    monkeypatch.setattr("app.rules.coc.checks.roll_percentile", lambda: 3)
    data = {"skills": {"急救": 50, "侦查": 50, "格斗(斗殴)": 50}, "base_attributes": {"CON": 50}}
    assert combat.resolve_first_aid(data, options=_CRIT5)["lines"][0].count("大成功")
    assert combat.resolve_observe(data, options=_CRIT5)["lines"][0].count("大成功")
    m = combat.resolve_maneuver(data, data, options=_CRIT5)
    assert "大成功" in m["lines"][0]


def test_chase_round_uses_options(monkeypatch):
    monkeypatch.setattr("app.rules.coc.checks.roll_percentile", lambda: 3)
    q = {"skills": {"运动": 60}, "base_attributes": {}}
    r = chase.resolve_chase_round(q, q, options=_CRIT5)
    assert r["quarry_check"].outcome == "critical_success"
    assert r["pursuer_check"].outcome == "critical_success"


def test_combat_service_passes_session_options(db_factory, monkeypatch):
    """村规 critical_max=5：真人攻击掷 03 → 战斗攻击检定判大成功。"""
    db = db_factory(); _, hero, sid = _seed(db)
    rule_options_service.save_village_options(db, "coc", {"critical_max": 5})
    enemy = {"id": "npc_thug", "name": "打手", "attributes": {"DEX": 40, "CON": 50, "SIZ": 60},
             "skills": {"格斗(斗殴)": 45, "闪避": 20}, "weapon": "徒手格斗"}
    state, _ = asyncio.run(combat_service.start(db, sid, [hero], [enemy], {hero.id}))
    spots = [(5, 5), (6, 5)]
    for p, (x, y) in zip(state["initiative"], spots):
        p["pos"] = {"x": x, "y": y}
    combat_service._save_combat(db, sid, state)

    seen = {}
    real = combat.resolve_attack

    def spy(*a, **k):
        r = real(*a, **k)
        seen["outcome"] = r["attacker_check"].outcome
        return r
    monkeypatch.setattr(combat, "resolve_attack", spy)
    monkeypatch.setattr("app.rules.coc.checks.roll_percentile", _seq([3, 90] + [50] * 20))
    monkeypatch.setattr("app.rules.coc.combat.random.randint", lambda a, b: 1)
    asyncio.run(combat_service.resolve_player_action(
        db, sid, hero.id, {"type": "attack", "target_id": "npc_thug", "weapon": "徒手格斗"},
    ))
    assert seen["outcome"] == "critical_success"


# ── 3. KP 提示词的疯狂口径与代码默认一致 ──────────────────────────────────


def test_kp_prompt_insanity_matches_default_rule():
    assert coc_options.DEFAULT_OPTIONS.insanity_rule == "fifth_of_san"
    assert coc_options.insanity_trigger_text(coc_options.DEFAULT_OPTIONS) in KP_SYSTEM_PROMPT
    assert "≥5 临时性疯狂" not in KP_SYSTEM_PROMPT


def test_describe_flat_insanity_uses_same_text():
    flat = coc_options.from_dict({"insanity_rule": "flat", "insanity_flat_threshold": 5})
    lines = coc_options.describe(flat)
    assert any(coc_options.insanity_trigger_text(flat) in line for line in lines)


# ── 4. 状态严重度表只有一份 ───────────────────────────────────────────────


def test_status_severity_single_source():
    from app.rules.coc.status import STATUS_SEVERITY

    from app.rules import ruleset_for

    assert not hasattr(turn_effects, "_STATUS_SEVERITY")
    # 业务层经生效规则取表（P1 收口后不再直接 import），拿到的必须就是 status.py 那一份
    assert ruleset_for("coc").status.STATUS_SEVERITY is STATUS_SEVERITY
