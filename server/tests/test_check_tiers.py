"""检定达成等级（六档）回归：纯按骰值 vs 技能值算出的 tier，与要求难度无关。"""

import app.rules.coc.checks as checks
from app.rules.coc.checks import achieved_tier, resolve_skill_check, TIER_LABEL_CN


def test_achieved_tier_thresholds():
    # 技能值 60：极难≤12，困难≤30，普通≤60
    assert achieved_tier(1, 60) == "critical"     # 01 恒大成功
    assert achieved_tier(10, 60) == "extreme"     # ≤12
    assert achieved_tier(25, 60) == "hard"        # ≤30
    assert achieved_tier(55, 60) == "regular"     # ≤60
    assert achieved_tier(70, 60) == "fail"        # >60 普通失败
    assert achieved_tier(100, 60) == "fumble"     # 100 大失败
    # 技能值 <50 时 96-99 也是大失败
    assert achieved_tier(97, 40) == "fumble"
    assert achieved_tier(97, 60) == "fail"        # 技能≥50 时 96-99 只是普通失败


def test_tier_is_independent_of_required_difficulty(monkeypatch):
    """同一骰值，要求难度变化只影响 meets_difficulty，不改 achieved tier。"""
    monkeypatch.setattr(checks, "roll_percentile", lambda: 25)  # 技能60 下属困难级
    cdata = {"skills": {"侦查": 60}, "base_attributes": {}}
    r_normal = resolve_skill_check(cdata, "侦查", "normal")
    r_hard = resolve_skill_check(cdata, "侦查", "hard")
    r_extreme = resolve_skill_check(cdata, "侦查", "extreme")
    assert r_normal.tier == r_hard.tier == r_extreme.tier == "hard"  # 达成等级恒为困难
    assert r_normal.meets_difficulty is True    # 25≤60 普通过线
    assert r_hard.meets_difficulty is True      # 25≤30 困难过线
    assert r_extreme.meets_difficulty is False  # 25>12 极难不过线


def test_extreme_distinguished_from_hard(monkeypatch):
    """旧实现里极难成功被并进困难；现在六档分明。"""
    monkeypatch.setattr(checks, "roll_percentile", lambda: 10)  # 技能60 下属极难级
    cdata = {"skills": {"侦查": 60}, "base_attributes": {}}
    assert resolve_skill_check(cdata, "侦查", "normal").tier == "extreme"
    assert TIER_LABEL_CN["extreme"] == "极难成功"


# ── 检定名的对外写法 ────────────────────────────────────────────────────


def test_英文属性键换成中文():
    """线上真实踩到过：KP 写了 `[DICE_CHECK: skill=STR]`，角色卡的 base_attributes
    本来就用英文键存，取值这层碰巧认得、检定算对了，显示这层却原样打给玩家，
    界面上就出现「请 X 进行一次「STR」检定」这种机器话。"""
    d = checks.display_skill_name
    assert d("STR") == "力量"
    assert d("POW") == "意志"
    assert d("LUCK") == "幸运"
    assert d("str") == "力量"          # 大小写不挑
    assert d("SAN") == "理智" and d("sanity") == "理智"


def test_中文与技能名原样奉还():
    """只做英文→中文一个方向，别顺手把别的也归一了。"""
    d = checks.display_skill_name
    assert d("力量") == "力量"
    assert d("图书馆使用") == "图书馆使用"
    assert d("射击(手枪)") == "射击(手枪)"
    assert d("") == "" and d(None) == ""


def test_灵感不被折成智力():
    """灵感按规则是 INT 直判，但它是个有名有姓的检定——显示成「智力」等于
    把 KP 的意图抹平。同理「战斗」不该在展示时被折成「格斗(斗殴)」。"""
    d = checks.display_skill_name
    assert d("灵感") == "灵感"
    assert d("知识") == "知识"
    assert d("战斗") == "战斗"


def test_换名之后仍然取得到值():
    """换的是显示名，不能把检定本身弄坏：力量和 STR 必须取到同一个值。"""
    attrs = {"STR": 60}
    cdata = {"base_attributes": attrs, "skills": {}, "system_data": {}}
    assert checks.resolve_skill_value(cdata, "STR") == checks.resolve_skill_value(cdata, "力量") == 60
