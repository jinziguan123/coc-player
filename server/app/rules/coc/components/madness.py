"""CoC 临时疯狂『一阵疯狂』症状表与影响判定（参照 7e『Bout of Madness — Summary』改编）。

进入临时疯狂时掷 1D10 随机一种症状：症状对**检定**（惩罚骰）与**言行**（叙事表现，
incapacitated 者发作期间无法正常自主行动、由系统/KP 接管）产生影响，持续 1D10 个回合后自动解除。
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from app.rules.config import RuleConfig, rule_field, table_of
from app.rules.data import DataTable
from app.rules.hooks import hookimpl
from app.rules.kernel import Component, Provide


class SymptomRow(BaseModel):
    """一种「一阵疯狂」症状。"""

    key: str = Field(min_length=1, pattern=r"^[a-z0-9_]+$", title="标识",
                     description="稳定英文标识（小写字母、数字、下划线），存进角色卡")
    label: str = Field(min_length=1, title="名称")
    manifest: str = Field(min_length=1, title="言行表现", description="一句话表现，注入叙事与播报")
    penalty_skills: list[str] = Field(default_factory=list, title="受惩罚的技能域",
                                      description="检定技能名命中其一即加一颗惩罚骰")
    incapacitated: bool = Field(False, title="发作期间失能", description="开启后由系统接管，KP 叙述其不自主的行为")
    override: Literal["", "violence", "faint", "flee"] = rule_field(
        "", title="接管时的强制行为", choices={
            "": ("无", "不强制行为"),
            "violence": ("暴力", "攻击身边的人"),
            "faint": ("昏厥", "倒地不起"),
            "flee": ("逃跑", "不顾一切地逃离"),
        },
    )


SYMPTOMS = DataTable(
    name="madness_symptoms", title="疯狂症状表", row_model=SymptomRow, key="key",
    source=Path(__file__).parent / "data" / "madness_symptoms.yaml",
    description="进入临时疯狂时随机抽取的症状",
    display="label",
    brief=lambda r: r["manifest"] + ("，发作期间失能" if r["incapacitated"] else ""),
)

#: 官方症状表（未经任何补丁）。
BOUT_SYMPTOMS = SYMPTOMS.official


def symptoms(options: RuleConfig | None = None) -> tuple[dict, ...]:
    """本局的症状表（官方 + 模组 + 村规补丁）。"""
    return table_of(options, "madness_symptoms", BOUT_SYMPTOMS)


def roll_symptom(options: RuleConfig | None = None) -> dict:
    """随机抽一种一阵疯狂症状（官方表 10 种即 1D10；返回症状定义的浅拷贝）。"""
    table = symptoms(options) or BOUT_SYMPTOMS   # 房主把症状删光时退回官方表，别让发作掷不出来
    return dict(table[random.randint(0, len(table) - 1)])


def roll_duration() -> int:
    """一阵疯狂持续回合数：1D10。"""
    return random.randint(1, 10)


def make_bout(options: RuleConfig | None = None) -> dict:
    """生成一次发作的状态对象，存进 char.system_data['madness']；turns_left 到 0 自动解除。"""
    s = roll_symptom(options)
    return {
        "symptom": s["key"], "label": s["label"], "manifest": s["manifest"],
        "penalty_skills": s["penalty_skills"], "incapacitated": s["incapacitated"],
        "override": s["override"], "turns_left": roll_duration(),
    }


def check_penalty(madness: dict | None, skill: str) -> int:
    """该症状是否给这次检定加惩罚骰：技能名命中 penalty_skills 任一即 +1，否则 0。"""
    if not madness or not skill:
        return 0
    s = skill.strip()
    for dom in madness.get("penalty_skills") or []:
        if dom and (dom in s or s in dom):
            return 1
    return 0


@hookimpl
def check_modifiers(character_data: dict, skill_name: str) -> list[dict] | None:
    """临时疯狂发作中：症状波及这项技能 → 加一颗惩罚骰。对所有检定生效（玩家、AI 队友、NPC、
    对抗、战斗、追逐），不再只在 KP 发起的玩家检定上手工叠加。"""
    madness = (character_data.get("system_data") or {}).get("madness")
    n = check_penalty(madness, skill_name)
    if not n:
        return None
    label = str((madness or {}).get("label") or "").strip()
    return [{"kind": "penalty", "n": n, "reason": f"临时疯狂{f'·{label}' if label else ''}影响了这项技能"}]


COMPONENT = Component(
    id="coc7.madness", title="疯狂发作",
    requires=("coc7.sanity",),
    data=(SYMPTOMS,),
    hooks=__name__,
    provides={"madness": Provide(
        __name__, bound=("symptoms", "make_bout", "roll_symptom"),
        plain=("roll_duration", "check_penalty"),
    )},
)
