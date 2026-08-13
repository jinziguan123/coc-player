"""CoC 7 版建卡规则：年龄修正、派生值、幸运。

按核心规则书逐条对照。这些数字是规则本身，不是实现细节——改动它们等于改规则。
"""

from typing import NamedTuple

import pytest

from app.rules.coc.character import (
    EDU_MAX,
    apply_age_modifiers,
    asset_tier,
    compute_derived,
    damage_bonus,
    derive_assets,
    roll_luck,
)


class _Roll(NamedTuple):
    total: int


class _FixedRoller:
    """按脚本返回骰点，让「掷骰规则」变成可断言的。"""

    def __init__(self, *totals):
        self.queue = list(totals)

    def __call__(self, _notation):
        return _Roll(self.queue.pop(0) if self.queue else 1)


BASE = {"STR": 60, "CON": 60, "SIZ": 60, "DEX": 60,
        "APP": 60, "INT": 60, "POW": 60, "EDU": 60}


def _delta(before, after):
    return {k: after[k] - before[k] for k in before if after[k] != before[k]}


# ── 移动力 ──────────────────────────────────────────────────────────────


def test_移动力三档():
    """回归：旧实现第二档写成 or，吞掉了第三档，MOV 永远算不出 9。"""
    mov = lambda s, d, z: compute_derived(  # noqa: E731
        {"STR": s, "DEX": d, "SIZ": z, "CON": 50, "POW": 50})["move"]
    assert mov(80, 80, 40) == 9      # STR 与 DEX **都** > SIZ
    assert mov(30, 30, 80) == 7      # 都 < SIZ
    assert mov(80, 30, 50) == 8      # 一高一低
    assert mov(50, 80, 50) == 8      # 等于 SIZ 不算「大于」


def test_移动力年龄减值():
    kw = {"STR": 80, "DEX": 80, "SIZ": 40, "CON": 50, "POW": 50}
    assert compute_derived(kw, age=25)["move"] == 9
    assert compute_derived(kw, age=45)["move"] == 8
    assert compute_derived(kw, age=85)["move"] == 4


# ── 伤害加值 / 体格 ─────────────────────────────────────────────────────


@pytest.mark.parametrize("combined,expected", [
    (2, ("-2", -2)), (64, ("-2", -2)),
    (65, ("-1", -1)), (84, ("-1", -1)),
    (85, ("0", 0)), (124, ("0", 0)),
    (125, ("1d4", 1)), (164, ("1d4", 1)),
    (165, ("1d6", 2)), (204, ("1d6", 2)),
    (205, ("2d6", 3)), (284, ("2d6", 3)),
    (285, ("3d6", 4)), (364, ("3d6", 4)),
    (365, ("4d6", 5)), (444, ("4d6", 5)),
])
def test_伤害加值分档(combined, expected):
    assert damage_bonus(combined) == expected


def test_伤害加值超出表尾仍按每80点进档():
    """回归：旧实现 165 以上全归 1D6。玩家角色够不着，但 NPC 与怪物会。"""
    assert damage_bonus(500) == ("5d6", 6)
    assert damage_bonus(600) == ("6d6", 7)


# ── 年龄修正 ────────────────────────────────────────────────────────────


def test_二十到三十九岁只掷一次教育增强():
    out, notes = apply_age_modifiers(dict(BASE), 25, roller=_FixedRoller(100, 8))
    assert _delta(BASE, out) == {"EDU": 8}          # 体能不减
    assert len([n for n in notes if "教育增强" in n["label"]]) == 1


def test_教育增强失败则不涨():
    # D100=30 ≤ EDU=60 → 不提升
    out, notes = apply_age_modifiers(dict(BASE), 25, roller=_FixedRoller(30))
    assert out["EDU"] == 60
    assert "未提升" in notes[0]["detail"]


def test_教育增强封顶九十九():
    attrs = {**BASE, "EDU": 95}
    out, _ = apply_age_modifiers(attrs, 25, roller=_FixedRoller(100, 10))
    assert out["EDU"] == EDU_MAX


@pytest.mark.parametrize("age,physical,app,edu_rolls", [
    (45, 5, 5, 2),
    (55, 10, 10, 3),
    (65, 20, 15, 4),
    (75, 40, 20, 4),
    (85, 80, 25, 4),
])
def test_各年龄档的减值总额与教育增强次数(age, physical, app, edu_rolls):
    # 所有教育增强都掷失败（D100=1 ≤ EDU），把 EDU 变量隔离掉，只看减值
    out, notes = apply_age_modifiers(dict(BASE), age, roller=_FixedRoller(*([1] * edu_rolls)))
    lost = sum(BASE[k] - out[k] for k in ("STR", "CON", "DEX"))
    assert lost == physical, f"{age} 岁体能减值总额应为 {physical}"
    assert BASE["APP"] - out["APP"] == app
    assert len([n for n in notes if "教育增强" in n["label"]]) == edu_rolls


def test_未成年扣教育与体型_不扣体能():
    out, _ = apply_age_modifiers(dict(BASE), 17, roller=_FixedRoller())
    assert out["EDU"] == 55                                   # EDU −5
    assert (BASE["STR"] - out["STR"]) + (BASE["SIZ"] - out["SIZ"]) == 5   # STR/SIZ 合计 −5
    assert out["CON"] == 60 and out["DEX"] == 60              # 不走年龄衰退那套
    assert out["APP"] == 60


def test_九十岁以上沿用最老一档():
    out, _ = apply_age_modifiers(dict(BASE), 95, roller=_FixedRoller(1, 1, 1, 1))
    assert sum(BASE[k] - out[k] for k in ("STR", "CON", "DEX")) == 80


def test_减值不会把属性打到零以下():
    """0 在 CoC 里意味着已经不是活人了，建卡阶段不该产出这种卡。"""
    frail = {**BASE, "STR": 15, "CON": 15, "DEX": 15, "APP": 15}
    out, _ = apply_age_modifiers(frail, 85, roller=_FixedRoller(1, 1, 1, 1))
    for k in ("STR", "CON", "DEX", "APP"):
        assert out[k] >= 1, k


def test_修正是纯函数_不改入参():
    attrs = dict(BASE)
    apply_age_modifiers(attrs, 65, roller=_FixedRoller(1, 1, 1, 1))
    assert attrs == BASE


def test_明细带上骰点():
    """属性被系统悄悄改掉最招人疑，明细要能说清每一点是怎么来的。"""
    _out, notes = apply_age_modifiers(dict(BASE), 45, roller=_FixedRoller(90, 7, 90, 7))
    edu_notes = [n for n in notes if "教育增强" in n["label"]]
    assert "D100=90" in edu_notes[0]["detail"]
    assert any(n["delta"] < 0 for n in notes)      # 减值也在明细里


# ── 幸运 ────────────────────────────────────────────────────────────────


def test_幸运是3d6乘5():
    luck, rolls = roll_luck(25, roller=_FixedRoller(10))
    assert luck == 50 and rolls == [50]


def test_未成年幸运掷两次取高():
    luck, rolls = roll_luck(17, roller=_FixedRoller(8, 14))
    assert rolls == [40, 70]
    assert luck == 70


def test_成年只掷一次():
    _luck, rolls = roll_luck(20, roller=_FixedRoller(10, 18))
    assert len(rolls) == 1


# ── 派生值主干 ──────────────────────────────────────────────────────────


def test_派生值公式():
    d = compute_derived({"STR": 50, "CON": 60, "SIZ": 70, "DEX": 50, "POW": 55}, age=25)
    assert d["hitPoints"]["max"] == 13        # (60+70)/10
    assert d["magicPoints"]["max"] == 11      # 55/5
    assert d["sanity"]["current"] == 55       # = POW


# ── 信用评级换算 ────────────────────────────────────────────────────────
#
# 整张表钉在这里，与前端 useCocData.test.ts 的用例逐格对应：同一张表在两侧各有一份
# （编辑器里的「按信用评级换算」要即时出数，不值得为查表走一次往返），任一侧漂移都得红。


@pytest.mark.parametrize(
    "cr,tier,spending,cash,assets",
    [
        # 信用, 等级,        消费水平, 现金,  资产
        (0, "一贫如洗", 0.5, 0.5, 0),
        (5, "贫穷", 2, 5, 50),
        (9, "贫穷", 2, 9, 90),
        (10, "普通", 10, 20, 500),
        (30, "普通", 10, 60, 1500),
        (49, "普通", 10, 98, 2450),
        (50, "富裕", 50, 250, 25000),
        (89, "富裕", 50, 445, 44500),
        (90, "富有", 250, 1800, 180000),
        (98, "富有", 250, 1960, 196000),
        (99, "巨富", 5000, 50000, 5000000),
    ],
)
def test_信用评级换算(cr, tier, spending, cash, assets):
    assert derive_assets(cr) == {
        "tier": tier, "spendingLevel": spending, "cash": cash, "assets": assets,
    }
    assert asset_tier(cr) == tier


def test_现金远小于资产():
    """现金是随身的钱，不是身家。

    前端那一列一度是 ×2/×20/×50/×100，比规则书高一个量级，普通阶层一上来就揣着
    六百刀。后端这份从一开始按修正后的倍率写，这条用来防它被「照着旧版抄回去」。
    """
    for cr in (5, 30, 70, 95):
        d = derive_assets(cr)
        assert d["cash"] < d["assets"]


# ── apply-age 接口 ──────────────────────────────────────────────────────


@pytest.fixture
def client():
    """纯规则计算端点，不碰数据库。"""
    from fastapi.testclient import TestClient

    from app.main import app
    return TestClient(app)


def test_接口返回修正后属性与明细(client):
    r = client.post("/api/rules/coc/apply-age", json={
        "base_attributes": dict(BASE), "age": 45,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["base_attributes"]["APP"] == 55           # 40-49 档 APP −5
    assert sum(BASE[k] - body["base_attributes"][k] for k in ("STR", "CON", "DEX")) == 5
    assert 15 <= body["luck"] <= 90
    assert body["luck_rolls"] == [body["luck"]]
    assert len([n for n in body["notes"] if "教育增强" in n["label"]]) == 2


def test_接口对未成年掷两次幸运(client):
    r = client.post("/api/rules/coc/apply-age", json={
        "base_attributes": dict(BASE), "age": 17,
    })
    body = r.json()
    assert len(body["luck_rolls"]) == 2
    assert body["luck"] == max(body["luck_rolls"])


def test_接口拒绝非coc规则(client):
    r = client.post("/api/rules/dnd/apply-age", json={
        "base_attributes": dict(BASE), "age": 30,
    })
    assert r.status_code == 400
