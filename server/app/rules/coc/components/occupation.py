"""coc7.occupation：职业与专精——职业表、职业技能点公式、专精技能类别。

两张数据表：``occupations``（官方数据见 data/occupations.yaml）与 ``specializations``
（data/specializations.yaml），房主/模组作者以补丁改动。槽位 ``rs.occupation``。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
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
class OccupationGroup:
    """选择组：从 ``options`` 里选 ``pick`` 项算作本职（如「一项社交技能」）。"""

    label: str
    pick: int
    options: list[str]


@dataclass
class Occupation:
    name: str
    credit_min: int
    credit_max: int
    skill_formula: str
    skills: list[str]            # 固定本职技能
    choices: int = 0             # 任意 N 项其他技能（规则书「任意 N 项个人或时代特长」）
    category: str = "其他"
    groups: list[OccupationGroup] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.groups = [g if isinstance(g, OccupationGroup) else OccupationGroup(**g) for g in self.groups]


class OccupationGroupRow(BaseModel):
    label: str = Field(min_length=1, title="名称", description="如「一项社交技能」")
    pick: int = Field(1, ge=1, le=10, title="选几项")
    options: list[str] = Field(min_length=1, title="可选技能")


class OccupationRow(BaseModel):
    name: str = Field(min_length=1, title="职业")
    category: str = Field("其他", min_length=1, title="大类")
    credit_min: int = Field(0, ge=0, le=99, title="信用评级下限")
    credit_max: int = Field(99, ge=0, le=99, title="信用评级上限")
    skill_formula: str = Field(
        "EDU*4", pattern=FORMULA_PATTERN, title="技能点公式",
        description="属性*倍数，用 + 相加，| 取较高者，如 EDU*2+STR|DEX*2",
    )
    skills: list[str] = Field(
        default_factory=list, title="固定本职技能",
        description="写基名（如「格斗」）表示该类任一专精都算",
    )
    groups: list[OccupationGroupRow] = Field(
        default_factory=list, title="选择组", description="如「一项社交技能」：从几项里选若干项算作本职",
    )
    choices: int = Field(0, ge=0, le=10, title="任意其他技能数", description="规则书的「任意 N 项个人或时代特长」")


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
    + (f"，本职技能 {'、'.join(r['skills'])}" if r["skills"] else "")
    + "".join(f"，{g['label']}" for g in r.get("groups") or ()),
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


def skill_base(name: str) -> str:
    """「格斗(斗殴)」→「格斗」。"""
    return name.split("(")[0].split("（")[0]


def _matches(listed: str, skill: str) -> bool:
    """职业表里写的技能是否覆盖角色卡上的这项：写基名（如「格斗」）覆盖该类任一专精。"""
    return listed == skill or (listed == skill_base(listed) and skill_base(skill) == listed)


def occupation_skill_role(occ: Occupation, skill: str) -> str:
    """这项技能对该职业是什么身份：``fixed`` 固定本职 / ``group:i`` 第 i 个选择组的候选 / ``free`` 其他。"""
    if any(_matches(s, skill) for s in occ.skills):
        return "fixed"
    for i, g in enumerate(occ.groups):
        if any(_matches(o, skill) for o in g.options):
            return f"group:{i}"
    return "free"


def check_occupation_picks(occ: Occupation, picks: list[str]) -> list[str]:
    """玩家对选择组 / 任意 N 项的勾选是否合规，返回问题列表（空 = 合规）。"""
    errors: list[str] = []
    if len(set(picks)) != len(picks):
        errors.append("本职技能勾选有重复")
    counts: dict[str, int] = {}
    for p in dict.fromkeys(picks):
        role = occupation_skill_role(occ, p)
        if role == "fixed":
            errors.append(f"「{p}」已是固定本职技能，不用再勾")
            continue
        counts[role] = counts.get(role, 0) + 1
    for i, g in enumerate(occ.groups):
        got = counts.get(f"group:{i}", 0)
        if got != g.pick:
            errors.append(f"{g.label}：应选 {g.pick} 项，选了 {got} 项")
    got = counts.get("free", 0)
    if got != occ.choices:
        errors.append(f"任意其他技能：应选 {occ.choices} 项，选了 {got} 项")
    return errors


def is_occupation_skill(occ: Occupation, picks: list[str], skill: str) -> bool:
    """勾选之后，这项技能算不算本职（信用评级恒算本职）。"""
    if skill == "信用评级":
        return True
    return occupation_skill_role(occ, skill) == "fixed" or skill in picks


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
        plain=(
            "calc_interest_points", "SINGLE_SPEC", "skill_base", "occupation_skill_role",
            "check_occupation_picks", "is_occupation_skill",
        ),
    )},
)
