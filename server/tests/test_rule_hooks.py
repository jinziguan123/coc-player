"""P4：规则钩子——疯狂统一作用于所有检定、Pulp 英雄生命值、流血、按开关的启用与互斥。"""

from __future__ import annotations

import pytest
from pydantic import create_model
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Character, GameSession, Module  # noqa: F401
from app.rules import RulesetError, ruleset_for
from app.rules.config import RuleConfig
from app.rules.hooks import hookimpl
from app.rules.kernel import Component, Provide, RuleSystem
from app.services import combat_service, rule_options_service

INSANE = {
    "skills": {"侦查": 60, "射击(手枪)": 60},
    "system_data": {"madness": {"label": "惊恐", "penalty_skills": ["侦查", "射击"]}},
}


@pytest.fixture(autouse=True)
def _fixed_roll(monkeypatch):
    monkeypatch.setattr("app.rules.coc.components.check.roll_percentile", lambda: 40)


# ── 疯狂：检定钩子，所有检定一视同仁 ──────────────────────────────────────


def test_madness_penalty_applies_through_any_ruleset_check():
    r = ruleset_for("coc").check.resolve_skill_check(INSANE, "侦查")
    assert r.penalty == 1 and r.modifiers == [
        {"kind": "penalty", "n": 1, "reason": "临时疯狂·惊恐影响了这项技能"},
    ]
    assert ruleset_for("coc").check.resolve_skill_check(INSANE, "聆听").penalty == 0


def test_madness_also_hits_combat_attacks():
    res = ruleset_for("coc").combat.resolve_attack(INSANE, "0", "手枪", ranged=True)
    assert res["attacker_check"].penalty == 1


def test_madness_penalty_respects_dice_pool_cap():
    r = ruleset_for("coc", {"dice_pool_cap": 0}).check.resolve_skill_check(INSANE, "侦查")
    assert r.penalty == 0


def test_raw_function_without_ruleset_has_no_hooks():
    from app.rules.coc.components.check import resolve_skill_check

    assert resolve_skill_check(INSANE, "侦查").penalty == 0


# ── Pulp 英雄生命值：替换一项计算 ─────────────────────────────────────────


def test_pulp_hp_replaces_formula_only_when_enabled():
    attrs = {"CON": 60, "SIZ": 60}
    assert ruleset_for("coc").character.max_hp_of(attrs) == 12
    pulp = ruleset_for("coc", {"pulp_hp": True})
    assert pulp.character.max_hp_of(attrs) == 24
    created = pulp.character.create_character({"base_attributes": {**attrs, "STR": 50, "DEX": 50,
                                                                   "APP": 50, "INT": 50, "POW": 50, "EDU": 50}})
    assert created["system_data"]["hitPoints"]["max"] == 24
    assert "Pulp 英雄生命值" in pulp.labels()


def test_pulp_hp_used_for_npcs_without_explicit_hp():
    p = combat_service._npc_participant({"name": "暴徒", "attributes": {"CON": 50, "SIZ": 50}},
                                        rs=ruleset_for("coc", {"pulp_hp": True}))
    assert p["max_hp"] == 20


# ── 流血：伤害之后挂状态、回合开始失血、急救止血 ──────────────────────────


def test_bleeding_attaches_on_major_wound_only_when_enabled():
    on = ruleset_for("coc", {"bleeding": True, "bleed_damage": 2})
    hit = on.wound.resolve_wound(12, 12, 7, {"base_attributes": {"CON": 99}})
    assert hit["conditions_add"] == ["bleeding"] and "每轮失血 2 点" in hit["lines"][-1]
    assert on.wound.resolve_wound(12, 12, 2, {})["conditions_add"] == []          # 轻伤不流血
    assert ruleset_for("coc").wound.resolve_wound(12, 12, 7, {"base_attributes": {"CON": 99}})[
        "conditions_add"] == []                                                       # 没开不流血
    assert on.wound.turn_start_damage({"conditions": ["bleeding"]}) == [{"damage": 2, "reason": "失血"}]
    assert on.wound.after_first_aid({"conditions": ["bleeding"]})["conditions_remove"] == ["bleeding"]


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'h.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_bleeding_in_combat_flow(db):
    m = Module(title="m", rule_system="coc")
    db.add(m)
    db.commit()
    gs = GameSession(module_id=m.id, status="active", world_state={})
    db.add(gs)
    db.commit()
    rule_options_service.save_village_options(db, "coc", {"bleeding": True})
    thug = combat_service._npc_participant({"name": "暴徒", "hp": 12, "attributes": {"CON": 99}})
    state = {"initiative": [thug], "round": 1, "log": []}
    combat_service.apply_damage(db, state, thug, 7, "刀伤", session_id=gs.id)
    assert "bleeding" in thug["conditions"] and thug["hp"] == 5
    rs = rule_options_service.ruleset(db, gs)
    assert rs.wound.turn_start_damage(thug) == [{"damage": 1, "reason": "失血"}]


# ── 按开关的启用、依赖、互斥（玩具规则系统）───────────────────────────────

_m = type("M", (), {"g": staticmethod(lambda: 1)})


def _flag_cfg(name: str):
    return create_model(f"Cfg_{name}", __base__=RuleConfig, **{name: (bool, False)})


def _toy():
    a = Component(id="a", title="甲", config=_flag_cfg("fa"), toggle="fa")
    b = Component(id="b", title="乙", config=_flag_cfg("fb"), toggle="fb", conflicts=("a",))
    c = Component(id="c", title="丙", config=_flag_cfg("fc"), toggle="fc", requires=("a",))
    opts = create_model("ToyOpts", __base__=(a.config, b.config, c.config))
    return RuleSystem(id="toy", title="toy", version=1, components=(a, b, c), preset=("a", "b", "c"),
                      options_model=opts)


def test_optional_conflicts_judged_by_toggles():
    sys_ = _toy()
    assert sys_.check_options({"fa": True}) == []
    assert sys_.check_options({"fa": True, "fb": True}) == ["「乙」与「甲」不能同时开启"]
    assert sys_.ruleset({"fa": True, "fb": True}).components == ("a",)       # 后者让位


def test_optional_dependency_judged_by_toggles():
    sys_ = _toy()
    assert sys_.check_options({"fc": True}) == ["「丙」需要先开启「甲」"]
    assert sys_.ruleset({"fc": True}).components == ()
    assert sys_.ruleset({"fa": True, "fc": True}).components == ("a", "c")


def test_hook_name_typo_fails_at_assembly():
    mod = type("Typo", (), {})
    mod.check_modifierz = hookimpl(lambda character_data: None)
    comp = Component(id="x", title="x", hooks=mod, provides={"x": Provide(_m, plain=("g",))})
    with pytest.raises(RulesetError, match="钩子无效"):
        RuleSystem(id="t", title="t", version=1, components=(comp,), preset=("x",),
                   options_model=create_model("Empty", __base__=RuleConfig))


def test_disabled_component_hooks_are_not_registered():
    assert "coc7.bleeding" not in ruleset_for("coc").components
    assert ruleset_for("coc").wound.turn_start_damage({"conditions": ["bleeding"]}) == []
