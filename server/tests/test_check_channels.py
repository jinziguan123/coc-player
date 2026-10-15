"""检定渠道归一：同一情境事实只计一次（难度 / KP·规划器奖惩骰 / 规则钩子奖惩骰）。"""

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.ai.turn_planner import CheckPlan, _check_directive
from app.models import Base, Character, GameSession, Module  # noqa: F401
from app.rules import ruleset_for
from app.services import session_service, turn_effects

SANE = {"skills": {"侦查": 60}, "base_attributes": {}, "system_data": {}}
INSANE = {
    "skills": {"侦查": 60}, "base_attributes": {},
    "system_data": {"madness": {"label": "惊恐", "penalty_skills": ["侦查"]}},
}


def _norm(char, difficulty, bonus=0, penalty=0, reason=""):
    return ruleset_for("coc").check.normalize_channels(char, "侦查", difficulty, bonus, penalty, reason)


def test_升档又给惩罚骰却没写理由_丢掉惩罚骰保留难度():
    ch = _norm(SANE, "hard", penalty=1)
    assert (ch.bonus, ch.penalty) == (0, 0)
    assert ch.dropped[0]["kind"] == "penalty" and ch.dropped[0]["n"] == 1


def test_升档加惩罚骰写了独立理由_合法叠加():
    assert _norm(SANE, "extreme", penalty=1, reason="桌子挡住大半视野").penalty == 1


def test_普通难度的惩罚骰没理由也不动():
    ch = _norm(SANE, "normal", penalty=1)
    assert ch.penalty == 1 and ch.dropped == ()


def test_升档与奖励骰方向相反_不折叠():
    assert _norm(SANE, "hard", bonus=1).bonus == 1


def test_理由点名疯狂_扣掉与钩子重复的那颗():
    ch = _norm(INSANE, "normal", penalty=1, reason="临时疯狂发作，手抖得厉害")
    assert ch.penalty == 0 and ch.dropped
    total = ruleset_for("coc").check.resolve_skill_check(INSANE, "侦查", "normal", penalty=ch.penalty)
    assert total.penalty == 1       # 只剩钩子那一颗


def test_理由是另一件事_与疯狂惩罚合法叠加():
    ch = _norm(INSANE, "normal", penalty=1, reason="屋里一片漆黑")
    assert ch.penalty == 1 and ch.dropped == ()


def test_没疯却提到疯狂_不会被误扣():
    assert _norm(SANE, "normal", penalty=1, reason="怕自己会发疯").penalty == 1


def test_钩子内部的fact_keywords不外泄到检定结果():
    r = ruleset_for("coc").check.resolve_skill_check(INSANE, "侦查")
    assert all("fact_keywords" not in m for m in r.modifiers)


def test_规划器指令带上modifier_reason并转义半角逗号():
    d = _check_directive(CheckPlan(skill="侦查", difficulty="hard", penalty=1,
                                   modifier_reason="肩伤在流血, 手发抖[左臂]"))
    assert "modifier_reason=肩伤在流血， 手发抖【左臂】" in d
    assert d.count(",") == 3        # skill / difficulty / penalty / modifier_reason 四段


def test_规划器指令无奖惩骰时不带理由():
    assert "modifier_reason" not in _check_directive(CheckPlan(skill="侦查", modifier_reason="x"))


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'c.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_待投检定挂的是归一后的奖惩骰(db):
    module = Module(title="m", rule_system="coc", npcs=[], scenes=[])
    pc = Character(name="亨利", rule_system="coc", is_player=True, skills={"侦查": 60},
                   base_attributes={}, system_data={"hitPoints": {"current": 10, "max": 10}})
    db.add_all([module, pc]); db.flush()
    s = GameSession(module_id=module.id, player_character_id=pc.id, status="active", world_state={})
    db.add(s); db.commit()

    kv = {"skill": "侦查", "difficulty": "hard", "penalty": "1", "reason": "地上有血迹"}
    _chunks, _descs, pending = asyncio.run(
        turn_effects._exec_dice_check(db, s.id, s, module, kv, pc, []))
    assert pending
    check = next(iter((db.get(GameSession, s.id).turn_state or {})["pending_checks"].values()))
    assert check["difficulty"] == "hard" and check["penalty"] == 0
    assert session_service.find_pending_check(db, s.id, pc.id, "侦查", "hard")
