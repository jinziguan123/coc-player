"""家规参数（L1）：读时覆盖、两层合并、越界钳制，以及各阈值确实改变了判定。"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Character, GameSession, Module  # noqa: F401
from app.rules.coc import options as coc_options
from app.rules.coc.checks import achieved_tier, is_fumble, san_check
from app.rules.coc.combat import resolve_wound
from app.rules.coc.engine import CoCRuleEngine
from app.services import rule_options_service


@pytest.fixture
def db_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'ruleopts.db'}", connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


# ── 取值规范化 ────────────────────────────────────────────────────────────


def test_defaults_are_raw():
    """空配置 = RAW，逐字与本特性上线前相同（存量存档不需要迁移）。"""
    opts = coc_options.from_dict(None)
    assert opts is coc_options.DEFAULT_OPTIONS
    assert opts.critical_max == 1
    assert opts.fumble_rule == "raw"
    assert opts.dice_pool_cap == 2
    assert opts.luck_spend is False


def test_unknown_keys_and_bad_values_are_dropped_or_clamped():
    opts = coc_options.from_dict({
        "critical_max": 999,          # 越界 → 钳到上限
        "dice_pool_cap": -3,          # 负数 → 钳到 0
        "fumble_rule": "乱写",         # 非法枚举 → 保持默认
        "insanity_flat_threshold": "5",  # 字符串数字 → 认
        "不认识的键": 1,
    })
    assert opts.critical_max == 20
    assert opts.dice_pool_cap == 0
    assert opts.fumble_rule == "raw"
    assert opts.insanity_flat_threshold == 5
    assert not hasattr(opts, "不认识的键")


def test_only_diffs_are_persisted():
    """只落与 RAW 的差异项：日后 RAW 默认值调整时，没显式改过的项跟着走。"""
    assert rule_options_service.normalized({"critical_max": 1}) == {}
    assert rule_options_service.normalized({"critical_max": 5}) == {"critical_max": 5}


def test_session_overrides_module_default(db_factory):
    db = db_factory()
    module = Module(
        title="M", rule_system="coc", npcs=[], scenes=[],
        default_rule_options={"critical_max": 5, "dice_pool_cap": 1},
    )
    db.add(module); db.flush()
    session = GameSession(
        module_id=module.id, status="active", world_state={},
        rule_options={"dice_pool_cap": 3},
    )
    db.add(session); db.commit()

    merged = rule_options_service.effective(db, db.get(GameSession, session.id))
    assert merged == {"critical_max": 5, "dice_pool_cap": 3}   # 会话盖模组，未提的继承


# ── 阈值真的改变了判定 ────────────────────────────────────────────────────


def test_fumble_rule_changes_verdict():
    # 技能 30、掷 96：RAW 判大失败（技能 < 50）
    assert is_fumble(96, 30) is True
    assert achieved_tier(96, 30) == "fumble"
    # 家规「只有 100 才大失败」→ 同一骰变成普通失败
    lenient = coc_options.from_dict({"fumble_rule": "hundred_only"})
    assert is_fumble(96, 30, lenient) is False
    assert achieved_tier(96, 30, lenient) == "fail"
    # 家规「96 起一律大失败」→ 高技能也翻车
    strict = coc_options.from_dict({"fumble_rule": "ninety_six_plus"})
    assert is_fumble(96, 80, strict) is True


def test_critical_threshold_widens():
    assert achieved_tier(4, 60) == "extreme"          # RAW：01 才是大成功
    widened = coc_options.from_dict({"critical_max": 5})
    assert achieved_tier(4, 60, widened) == "critical"


def test_dice_pool_cap_clamps_bonus(monkeypatch):
    """奖惩骰上限调到 0 → 传进来的奖励骰不再生效（只掷一个十位）。"""
    from app.rules.coc import checks as checks_mod

    monkeypatch.setattr(checks_mod, "roll_percentile", lambda: 55)
    capped = coc_options.from_dict({"dice_pool_cap": 0})
    result = checks_mod.resolve_skill_check(
        {"skills": {"侦查": 60}}, "侦查", bonus=2, options=capped,
    )
    assert result.bonus == 0
    assert len(result.tens) == 1


def test_insanity_rule_switches_to_flat(monkeypatch):
    """临时疯狂口径可切到 CoC 原文的「单次 ≥5 点」。"""
    from app.rules.coc import checks as checks_mod

    monkeypatch.setattr(checks_mod, "roll_percentile", lambda: 99)  # 必失败
    char = {"system_data": {"sanity": {"current": 60, "max": 99}}}
    # 固定损失 4：SAN/5=12，按现行口径不疯；按 flat(5) 也不疯
    assert san_check(char, "0", "4")["went_insane"] is False
    flat = coc_options.from_dict({"insanity_rule": "flat", "insanity_flat_threshold": 3})
    assert san_check(char, "0", "4", flat)["went_insane"] is True


def test_major_wound_divisor_changes_threshold():
    defender = {"skills": {}, "base_attributes": {"CON": 50}}
    # 满血 12、受 5 点：RAW 半血阈值 6 → 不算重伤
    assert resolve_wound(12, 12, 5, defender)["status"] == "ok"
    # 家规改成 1/3 → 阈值 4，同一击算重伤（走体质检定，可能昏迷或重伤）
    thirds = coc_options.from_dict({"major_wound_divisor": 3})
    assert resolve_wound(12, 12, 5, defender, options=thirds)["status"] in (
        "major_wound", "unconscious",
    )


def test_improvement_can_be_switched_off():
    engine = CoCRuleEngine()
    assert engine.improvement_check(50) is not None
    assert engine.improvement_check(50, {"improvement": False}) is None
