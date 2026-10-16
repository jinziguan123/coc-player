"""coc7.creation：建卡规则——三种属性生成方式、年龄修正分配、本职技能勾选、点数与上限的校验。

槽位 ``rs.creation``。村规参数见 :class:`CreationConfig`（建卡技能上限）。

校验只看「建出来的卡 + 建卡记录」，不需要知道每一点来自哪个点池：技能最终值减去起始值
（母语=EDU、闪避=DEX/2 由属性现算）就是花在它上面的点；兴趣点能加任何技能、职业点只能
加本职技能，于是「非本职加点 ≤ 兴趣点」且「本职加点 + 信用评级 ≤ 职业点 + 剩下的兴趣点」
即为可行。建卡记录随卡保存在 ``system_data.creation``，以后查卡能看出它是怎么建出来的。
"""

from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import BaseModel, Field

from app.rules.coc.components import character as ch
from app.rules.coc.components import occupation as occ_mod
from app.rules.config import RuleConfig, rule_field
from app.rules.kernel import Component, Provide

ATTR_KEYS = ("STR", "CON", "SIZ", "DEX", "APP", "INT", "POW", "EDU")
PLUS_SIX = {"SIZ", "INT", "EDU"}

#: 点数购买的总额与单项区间（与前端一致）。
POINT_BUY_TOTAL = 460
#: 快速车卡：八项属性分配这八个值；本职技能填这九个值；四项兴趣各 +20（规则书「快速车卡」）。
QUICK_ATTRS = (40, 50, 50, 50, 60, 60, 70, 80)
QUICK_SKILLS = (70, 60, 60, 50, 50, 50, 40, 40, 40)
QUICK_BOOSTS = 4
QUICK_BOOST = 20

#: 建卡时不可加点的技能。
LOCKED_SKILLS = {"克苏鲁神话"}
CREDIT = "信用评级"


class CreationConfig(RuleConfig):
    """coc7.creation：建卡技能上限。"""

    skill_cap_occupation: int = rule_field(
        90, ge=1, le=99, title="本职技能建卡上限",
        hint="建卡加点最多加到多少；起始值本就更高的（如母语=教育）不算违规，只是不能再加",
    )
    skill_cap_other: int = rule_field(
        90, ge=1, le=99, title="其他技能建卡上限", hint="常见房规是本职 75、其他 50",
    )

    def describe(self) -> list[str]:
        if (self.skill_cap_occupation, self.skill_cap_other) == (90, 90):
            return []
        return [f"建卡技能上限：本职 {self.skill_cap_occupation}，其他 {self.skill_cap_other}"]

    def labels(self) -> list[str]:
        if (self.skill_cap_occupation, self.skill_cap_other) == (90, 90):
            return []
        return [f"建卡上限 {self.skill_cap_occupation}/{self.skill_cap_other}"]


_RAW = CreationConfig()


class CreationRecord(BaseModel):
    """建卡记录：前端随建卡请求提交，校验后存进 ``system_data.creation``。"""

    method: Literal["roll", "buy", "quick"] = Field(title="属性生成方式")
    base_attributes: dict[str, int] = Field(title="年龄修正前的属性")
    age: int = Field(ge=15, le=89)
    #: 年龄修正里玩家自己分配的扣点（少年：STR/SIZ 合计 5；40 岁起：STR/CON/DEX）。
    age_deductions: dict[str, int] = Field(default_factory=dict)
    occupation: str
    #: 选择组与「任意 N 项」里勾的技能（固定本职技能不用列）。
    occupation_picks: list[str] = Field(default_factory=list)
    #: 快速车卡：加了 +20 的四项兴趣技能。
    quick_boosts: list[str] = Field(default_factory=list)


def age_band(age: int) -> dict:
    """该年龄段的修正：EDU 增强次数、要扣的属性与总额、外貌减值、少年 EDU−5。"""
    if age <= 19:
        return {"edu_checks": 0, "deduct_from": ("STR", "SIZ"), "deduct": 5, "app": 0, "edu_penalty": 5}
    for ceiling, checks, physical, app in ch._AGE_BANDS[1:]:
        if age <= ceiling:
            return {"edu_checks": checks, "deduct_from": ("STR", "CON", "DEX") if physical else (),
                    "deduct": physical, "app": app, "edu_penalty": 0}
    return {"edu_checks": 4, "deduct_from": ("STR", "CON", "DEX"), "deduct": 80, "app": 25, "edu_penalty": 0}


def required_deduction(age: int, base: dict[str, int]) -> int:
    """该年龄要扣的体能总额。每项最多扣到 1：几项都扣到底还不够时，以能扣的为准。"""
    band = age_band(age)
    room = sum(max(0, base.get(k, 0) - ch._ATTR_FLOOR) for k in band["deduct_from"])
    return min(band["deduct"], room)


def _check_base_attributes(method: str, base: dict[str, int]) -> list[str]:
    errors: list[str] = []
    missing = [k for k in ATTR_KEYS if k not in base]
    if missing:
        return [f"缺少属性：{'、'.join(missing)}"]
    vals = {k: base[k] for k in ATTR_KEYS}
    if method == "quick":
        if sorted(vals.values()) != sorted(QUICK_ATTRS):
            errors.append(f"快速车卡的属性必须是 {'/'.join(map(str, QUICK_ATTRS))} 各用一次")
        return errors
    for k, v in vals.items():
        lo = 40 if k in PLUS_SIX else 15
        if not lo <= v <= 90:
            errors.append(f"{k} {v} 超出 {lo}–90")
        elif method == "roll" and v % 5:
            errors.append(f"{k} {v} 不是掷骰能得到的值")
    if method == "buy" and sum(vals.values()) > POINT_BUY_TOTAL:
        errors.append(f"点数购买共 {sum(vals.values())} 点，超出 {POINT_BUY_TOTAL}")
    return errors


def _check_age(rec: CreationRecord, final: dict[str, int]) -> list[str]:
    """年龄修正：扣点分配合规，且最终属性与「修正前属性 − 扣点」对得上（EDU 允许增强检定的增幅）。"""
    errors: list[str] = []
    band = age_band(rec.age)
    ded = {k: v for k, v in rec.age_deductions.items() if v}
    stray = [k for k in ded if k not in band["deduct_from"]]
    if stray:
        errors.append(f"{rec.age} 岁不能从 {'、'.join(stray)} 扣点")
    if any(v < 0 for v in ded.values()):
        errors.append("年龄扣点不能为负")
    base = rec.base_attributes
    for k, v in ded.items():
        if k in base and v > base[k] - ch._ATTR_FLOOR:
            errors.append(f"{k} 最多扣到 {ch._ATTR_FLOOR}，不能扣 {v} 点")
    need = required_deduction(rec.age, base)
    if sum(ded.values()) != need:
        errors.append(f"{rec.age} 岁要从 {'、'.join(band['deduct_from']) or '属性'} 合计扣 {need} 点，"
                      f"现在分配了 {sum(ded.values())} 点")
    for k in ATTR_KEYS:
        if k == "EDU":
            continue
        want = base.get(k, 0) - ded.get(k, 0) - (band["app"] if k == "APP" else 0)
        want = max(ch._ATTR_FLOOR, want)
        if final.get(k) != want:
            errors.append(f"{k} 应为 {want}（修正前 {base.get(k)}），卡上是 {final.get(k)}")
    edu0 = base.get("EDU", 0) - band["edu_penalty"]
    hi = min(ch.EDU_MAX, edu0 + 10 * band["edu_checks"])
    if not edu0 <= final.get("EDU", 0) <= hi:
        errors.append(f"教育 {final.get('EDU')} 超出年龄修正后可能的范围 {edu0}–{hi}")
    return errors


def skill_start(skill: str, attrs: dict[str, int], options: RuleConfig | None = None) -> int:
    """这项技能未加点时的起始值：母语=EDU、闪避=DEX/2、专精按专精表、其余按技能表。"""
    base = occ_mod.skill_base(skill)
    if base == "母语":
        return attrs.get("EDU", 0)
    if skill == "闪避":
        return attrs.get("DEX", 0) // 2
    table = ch.default_skills(options)
    if skill in table:
        return table[skill]
    spec = occ_mod.specializations(options).get(base)
    if spec is not None:
        inner = skill[len(base):].strip("()（）")
        for item in spec["items"]:
            if item["name"] == inner:
                return item["init"]
        return spec["base_init"]
    return table.get(base, 0)


def validate_build(
    record: dict | CreationRecord,
    base_attributes: dict[str, int],
    skills: dict[str, int],
    options: RuleConfig | None = None,
) -> list[str]:
    """按建卡记录校验一张新卡，返回问题列表（空 = 合规）。"""
    cfg = options if isinstance(options, CreationConfig) else _RAW
    try:
        rec = record if isinstance(record, CreationRecord) else CreationRecord.model_validate(record)
    except Exception as e:  # noqa: BLE001 — 记录格式错直接作为一条问题返回
        return [f"建卡记录格式不对：{e}"]
    errors = _check_base_attributes(rec.method, rec.base_attributes)
    if errors:
        return errors
    final = {k: base_attributes.get(k, 0) for k in ATTR_KEYS}
    errors += _check_age(rec, final)

    occ = occ_mod.get_occupation(rec.occupation, options)
    if occ is None:
        return [*errors, f"职业「{rec.occupation}」不在本局职业表里"]
    errors += occ_mod.check_occupation_picks(occ, rec.occupation_picks)
    is_occ = lambda s: occ_mod.is_occupation_skill(occ, rec.occupation_picks, s)  # noqa: E731

    credit = skills.get(CREDIT, 0)
    if not occ.credit_min <= credit <= occ.credit_max:
        errors.append(f"信用评级 {credit} 不在「{occ.name}」的 {occ.credit_min}–{occ.credit_max} 内")

    alloc: dict[str, int] = {}
    for name, value in skills.items():
        if name == CREDIT:
            continue
        start = skill_start(name, final, options)
        spent = value - start
        if spent < 0:
            errors.append(f"{name} {value} 低于起始值 {start}")
        elif spent and name in LOCKED_SKILLS:
            errors.append(f"{name} 建卡时不能加点")
        elif spent:
            alloc[name] = spent
            cap = cfg.skill_cap_occupation if is_occ(name) else cfg.skill_cap_other
            if value > max(cap, start):
                errors.append(f"{name} 加到 {value}，超过建卡上限 {cap}")

    if rec.method == "quick":
        errors += _check_quick(rec, final, skills, alloc, is_occ, options)
        return errors

    occ_points = occ_mod.calc_occupation_points(occ.name, final, options)
    int_points = occ_mod.calc_interest_points(final)
    spent_other = sum(v for k, v in alloc.items() if not is_occ(k))
    spent_occ = sum(v for k, v in alloc.items() if is_occ(k))
    if credit > occ_points:
        errors.append(f"信用评级 {credit} 超过职业点 {occ_points}（信用评级只能用职业点）")
    if spent_other > int_points:
        errors.append(f"非本职技能共加 {spent_other} 点，超过兴趣点 {int_points}")
    elif spent_occ + credit > occ_points + (int_points - spent_other):
        errors.append(f"本职技能与信用评级共用 {spent_occ + credit} 点，超过职业点 {occ_points}"
                      f" + 剩余兴趣点 {int_points - spent_other}")
    return errors


def _check_quick(rec, final, skills, alloc, is_occ, options) -> list[str]:
    """快速车卡：本职技能取九个定值之一（不低于起始值），四项非本职 +20，其余保持起始值。"""
    errors: list[str] = []
    boosts = set(rec.quick_boosts)
    if len(boosts) > QUICK_BOOSTS:
        errors.append(f"快速车卡只能给 {QUICK_BOOSTS} 项兴趣技能 +{QUICK_BOOST}")
    used: list[int] = []
    for name, spent in alloc.items():
        value = skills[name]
        if is_occ(name):
            used.append(value)
        elif name in boosts:
            if spent != QUICK_BOOST:
                errors.append(f"{name} 应为起始值 +{QUICK_BOOST}")
        else:
            errors.append(f"{name} 不是本职技能也没选为兴趣 +{QUICK_BOOST}，不能加点")
    if Counter(used) - Counter(QUICK_SKILLS):
        errors.append(f"本职技能的值只能从 {'/'.join(map(str, QUICK_SKILLS))} 里各取一次")
    return errors


COMPONENT = Component(
    id="coc7.creation", title="建卡",
    requires=("coc7.character", "coc7.occupation"),
    config=CreationConfig, group="建卡",
    provides={"creation": Provide(
        __name__,
        bound=("validate_build", "skill_start"),
        plain=(
            "CreationRecord", "age_band", "required_deduction", "POINT_BUY_TOTAL", "QUICK_ATTRS", "QUICK_SKILLS",
            "QUICK_BOOSTS", "QUICK_BOOST",
        ),
    )},
)
