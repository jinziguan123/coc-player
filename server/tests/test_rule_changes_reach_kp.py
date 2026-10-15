"""规则改动在叙事里也要生效、KP 也要知道：数据表改动告知 KP；叙事扣血也走伤害钩子（流血）。"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Character, GameSession, Module  # noqa: F401
from app.models.session_participant import SessionParticipant
from app.services import chat_service, combat_service, rule_options_service
from tests.wire import wires

THUNDER = {"name": "雷霆之锤", "skill": "格斗(斗殴)", "dam": "2D8+DB"}


@pytest.fixture
def db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'k.db'}", connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


@pytest.fixture(autouse=True)
def _con_passes(monkeypatch):
    """重伤体质检定一律过：只看流血，不让昏迷分支掺进来。"""
    monkeypatch.setattr("app.rules.coc.components.check.roll_percentile", lambda: 1)


def _seed(db, *, module_data: dict | None = None, hp: int = 12):
    module = Module(title="M", rule_system="coc", npcs=[], scenes=[], default_rule_data=module_data or {})
    pc = Character(
        name="伊芙琳", rule_system="coc", is_player=True,
        base_attributes={"CON": 60, "DEX": 50}, skills={},
        system_data={"hitPoints": {"current": hp, "max": hp}}, status="active",
    )
    db.add_all([module, pc]); db.flush()
    gs = GameSession(module_id=module.id, player_character_id=pc.id, status="active", world_state={})
    db.add(gs); db.flush()
    db.add(SessionParticipant(session_id=gs.id, character_id=pc.id, role="human"))
    db.commit()
    return module, pc, gs


# ── 1. 数据表改动告知 KP ──────────────────────────────────────────────────


def test_no_data_change_keeps_block_empty(db):
    _, _, gs = _seed(db)
    assert rule_options_service.ruleset(db, gs).data_changes() == []
    assert rule_options_service.context_block(db, gs) == ""


def test_custom_weapon_is_told_to_kp(db):
    _, _, gs = _seed(db)
    rule_options_service.save_village_options(db, "coc", {}, data={"weapons": [THUNDER]})
    block = rule_options_service.context_block(db, gs)
    assert "【本桌改过的数据表】" in block
    assert "武器表：新增 雷霆之锤（格斗(斗殴)，伤害 2D8+DB）" in block
    assert "【本桌改过的规则】" not in block     # 参数没改，那一段不出


def test_module_and_village_layers_both_counted(db):
    """模组自带的改动与村规的改动都算：KP 看的是这一局最终生效的表。"""
    _, _, gs = _seed(db, module_data={"weapons": [{"name": "伐木斧", "dam": "1D10+DB"}]})
    rule_options_service.save_village_options(db, "coc", {}, data={"weapons": [THUNDER]})
    block = rule_options_service.context_block(db, gs)
    assert "新增 雷霆之锤" in block
    assert "修改 伐木斧（" in block and "1D10+DB" in block


def test_madness_symptom_shows_label_and_manifest(db):
    _, _, gs = _seed(db)
    rule_options_service.save_village_options(db, "coc", {}, data={"madness_symptoms": [
        {"key": "sing", "label": "放声高歌", "manifest": "不分场合地唱起童谣"},
    ]})
    block = rule_options_service.context_block(db, gs)
    assert "疯狂症状表：新增 放声高歌（不分场合地唱起童谣）" in block


def test_data_changes_capped_names_then_count():
    many = [("武器表", [(f"新增 武器{i}（格斗(斗殴)，伤害 1D6+DB）", f"新增 武器{i}") for i in range(80)])]
    text = rule_options_service.data_changes_text(many, limit=200)
    assert len(text) <= 200
    assert "武器0" in text and "伤害" not in text      # 超长先退成只列名字
    assert text.endswith("项") and "等 " in text        # 还超就「等 N 项」

    few = [("武器表", [("新增 雷霆之锤（格斗(斗殴)，伤害 2D8+DB）", "新增 雷霆之锤")])]
    assert rule_options_service.data_changes_text(few) == "- 武器表：新增 雷霆之锤（格斗(斗殴)，伤害 2D8+DB）"


# ── 2. 叙事扣血也走伤害钩子（流血）────────────────────────────────────────


def _hp_change(db, module, pc, gs, delta: str, reason: str = "刀伤"):
    return wires(asyncio.run(chat_service._exec_hp_change(
        db, gs.id, pc, "player", delta, reason, module=module,
    )))


def _hp(pc):
    return pc.system_data["hitPoints"]["current"]


def test_narrative_major_wound_attaches_bleeding(db):
    module, pc, gs = _seed(db)
    rule_options_service.save_village_options(db, "coc", {"bleeding": True})
    chunks = _hp_change(db, module, pc, gs, "-7")
    assert pc.system_data["conditions"] == ["bleeding"]
    assert any("血流不止" in c for c in chunks)
    # 战斗外不自动扣血：KP 从上下文得知谁在流血、怎么结算
    assert _hp(pc) == 5
    block = rule_options_service.context_block(db, gs)
    assert "【角色身上的持续状态】" in block and "伊芙琳｜流血中" in block and "不自动扣血" in block


def test_narrative_minor_wound_or_bleeding_off_adds_nothing(db):
    module, pc, gs = _seed(db)
    _hp_change(db, module, pc, gs, "-7")                  # 流血没开
    assert not pc.system_data.get("conditions")
    rule_options_service.save_village_options(db, "coc", {"bleeding": True})
    _hp_change(db, module, pc, gs, "-1")                  # 开了，但只是轻伤
    assert not pc.system_data.get("conditions")


def test_narrative_first_aid_stops_bleeding(db):
    module, pc, gs = _seed(db)
    rule_options_service.save_village_options(db, "coc", {"bleeding": True})
    _hp_change(db, module, pc, gs, "-7")
    chunks = wires(chat_service._apply_heal_on_success(db, gs.id, pc, "急救", "success"))
    assert pc.system_data["conditions"] == []
    assert any("血止住了" in c for c in chunks)
    assert "持续状态" not in rule_options_service.context_block(db, gs)


def test_bleeding_carries_into_combat_and_back(db):
    module, pc, gs = _seed(db)
    rule_options_service.save_village_options(db, "coc", {"bleeding": True})
    _hp_change(db, module, pc, gs, "-7")
    rs = rule_options_service.ruleset(db, gs)
    p = combat_service._char_participant(pc, "player", rs=rs)
    assert p["conditions"] == ["bleeding"]
    assert rs.wound.turn_start_damage(p) == [{"damage": 1, "reason": "失血"}]


def test_combat_major_wound_bleeding_persists_on_card(db):
    """战斗里挂上的流血写回角色卡（战后仍在）；擒抱这类战斗态自有的状态不落卡。"""
    _, pc, gs = _seed(db)
    rule_options_service.save_village_options(db, "coc", {"bleeding": True})
    p = combat_service._char_participant(pc, "player", rs=rule_options_service.ruleset(db, gs))
    p["conditions"] = ["grappled"]
    combat_service.apply_damage(db, {"initiative": [p], "round": 1, "log": []}, p, 7, "刀伤", session_id=gs.id)
    db.refresh(pc)
    assert p["conditions"] == ["grappled", "bleeding"]
    assert pc.system_data["conditions"] == ["bleeding"]
