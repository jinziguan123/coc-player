"""coc7.occupation：职业与专精——职业表、职业技能点公式、专精技能类别。

两张数据表：``occupations``（官方数据见 data/occupations.yaml）与 ``specializations``
（data/specializations.yaml），房主/模组作者以补丁改动。槽位 ``rs.occupation``。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from app.rules.config import RuleConfig, table_of
from app.rules.data import DataTable
from app.rules.kernel import Component, Provide

_DATA = Path(__file__).parent / "data"
_ATTRS = ("STR", "CON", "SIZ", "DEX", "APP", "INT", "POW", "EDU")

#: 职业技能点公式的合法形式：「属性*倍数」用 + 连接，| 表示取几项属性中较高者。
#: 例：EDU*4、EDU*2+DEX*2、EDU*2+STR|DEX*2。只认这一种写法——房主填的公式绝不 eval。
_TERM = rf"(?:{'|'.join(_ATTRS)})(?:\|(?:{'|'.join(_ATTRS)}))*\*\d+"
FORMULA_PATTERN = rf"^{_TERM}(?:\+{_TERM})*$"


@dataclass
class Occupation:
    name: str
    credit_min: int
    credit_max: int
    skill_formula: str
    skills: list[str]
    choices: int = 0  # 可自选技能数
    category: str = "其他"


class OccupationRow(BaseModel):
    name: str = Field(min_length=1, title="职业")
    category: str = Field("其他", min_length=1, title="大类")
    credit_min: int = Field(0, ge=0, le=99, title="信用评级下限")
    credit_max: int = Field(99, ge=0, le=99, title="信用评级上限")
    skill_formula: str = Field(
        "EDU*4", pattern=FORMULA_PATTERN, title="技能点公式",
        description="属性*倍数，用 + 相加，| 取较高者，如 EDU*2+STR|DEX*2",
    )
    skills: list[str] = Field(default_factory=list, title="本职技能")
    choices: int = Field(0, ge=0, le=10, title="可自选技能数")


class SpecializationItem(BaseModel):
    name: str = Field(min_length=1, title="专精")
    init: int = Field(0, ge=0, le=99, title="起始值")


class SpecializationRow(BaseModel):
    category: str = Field(min_length=1, title="类别", description="如 外语、格斗、射击、科学")
    base_init: int = Field(0, ge=0, le=99, title="其他专精起始值", description="未在下面列出的专精用这个起始值")
    items: list[SpecializationItem] = Field(default_factory=list, title="专精列表")


OCCUPATIONS = DataTable(
    name="occupations", title="职业表", row_model=OccupationRow, key="name",
    source=_DATA / "occupations.yaml", description="建卡时可选的职业、技能点公式与本职技能",
    build=lambda rows: tuple(Occupation(**r) for r in rows),
    brief=lambda r: f"信用评级 {r['credit_min']}-{r['credit_max']}"
    + (f"，本职技能 {'、'.join(r['skills'])}" if r["skills"] else ""),
)
SPECIALIZATIONS_TABLE = DataTable(
    name="specializations", title="专精表", row_model=SpecializationRow, key="category",
    source=_DATA / "specializations.yaml", description="外语、格斗、射击等技能下可选的专精及起始值",
    build=lambda rows: {r["category"]: {"base_init": r["base_init"], "items": r["items"]} for r in rows},
    brief=lambda r: "、".join(
        [f"{i['name']} {i['init']}" for i in r["items"][:6]]
        + ([f"等 {len(r['items'])} 项"] if len(r["items"]) > 6 else [])
        + [f"其他专精起始值 {r['base_init']}"]
    ),
)

#: 官方数据（未经任何补丁）。结算请经下面的函数取本局的表。
COC_OCCUPATIONS = OCCUPATIONS.official
SPECIALIZATIONS = SPECIALIZATIONS_TABLE.official

# 职业大致分类（用于两级选择 UI）
OCCUPATION_CATEGORY_ORDER: list[str] = [
    "文艺工作",
    "工农商",
    "职工",
    "学者",
    "医疗系统",
    "运动/探险",
    "服务业",
    "宗教相关",
    "灰色/无职",
    "罪犯",
    "军警政",
]


# 母语唯一（你的本族语言，值=EDU）；其余可有多个专精实例
SINGLE_SPEC = {"母语"}


def occupations(options: RuleConfig | None = None) -> tuple[Occupation, ...]:
    """本局的职业表（官方 + 模组 + 村规补丁）。"""
    return table_of(options, "occupations", COC_OCCUPATIONS)


def occupation_categories(options: RuleConfig | None = None) -> list[str]:
    """职业大类的展示顺序：官方大类在前，房主新增的按出现顺序追加。"""
    extra = [o.category for o in occupations(options) if o.category not in OCCUPATION_CATEGORY_ORDER]
    return OCCUPATION_CATEGORY_ORDER + list(dict.fromkeys(extra))


def specializations(options: RuleConfig | None = None) -> dict[str, dict]:
    """本局的专精表：类别 → {base_init, items}。"""
    return table_of(options, "specializations", SPECIALIZATIONS)


def get_occupation(name: str, options: RuleConfig | None = None) -> Occupation | None:
    for occ in occupations(options):
        if occ.name == name:
            return occ
    return None


def _calc_points(formula: str, attrs: dict[str, int]) -> int:
    """解析公式如 'EDU*4', 'EDU*2+DEX*2', 'EDU*2+STR|DEX*2'"""
    attr_map = {
        "EDU": attrs.get("EDU", 0),
        "STR": attrs.get("STR", 0),
        "DEX": attrs.get("DEX", 0),
        "APP": attrs.get("APP", 0),
        "POW": attrs.get("POW", 0),
        "INT": attrs.get("INT", 0),
        "CON": attrs.get("CON", 0),
        "SIZ": attrs.get("SIZ", 0),
    }
    parts = formula.split("+")
    total = 0
    for part in parts:
        part = part.strip()
        if "|" in part:
            # "STR|DEX*2" → max(STR, DEX) * 2
            m = re.match(r"([\w|]+)\*(\d+)", part)
            if m:
                attr_names = m.group(1).split("|")
                mult = int(m.group(2))
                total += max(attr_map.get(a.strip(), 0) for a in attr_names) * mult
        else:
            attr, mult = part.split("*")
            total += attr_map.get(attr.strip(), 0) * int(mult)
    return total


def calc_occupation_points(
    occupation_name: str, attrs: dict[str, int], options: RuleConfig | None = None,
) -> int:
    occ = get_occupation(occupation_name, options)
    if not occ:
        return attrs.get("EDU", 0) * 4
    return _calc_points(occ.skill_formula, attrs)


def calc_interest_points(attrs: dict[str, int]) -> int:
    return attrs.get("INT", 0) * 2


COMPONENT = Component(
    id="coc7.occupation", title="职业与专精",
    requires=("coc7.character",),
    data=(OCCUPATIONS, SPECIALIZATIONS_TABLE),
    provides={"occupation": Provide(
        __name__,
        bound=(
            "occupations", "occupation_categories", "specializations", "get_occupation",
            "calc_occupation_points",
        ),
        plain=("calc_interest_points", "SINGLE_SPEC"),
    )},
)
