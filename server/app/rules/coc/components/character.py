"""coc7.character：属性、年龄修正、派生值、技能起始值，以及建卡入口。

槽位 ``rs.character``。本组件没有村规参数。
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, Field

from app.rules.config import RuleConfig, table_of
from app.rules.data import DataTable, frozen_mapping
from app.rules.dice import roll
from app.rules.hooks import hookimpl, hooks_of
from app.rules.kernel import Component, Provide

# CoC 7th 基础属性掷骰规则
ATTRIBUTE_ROLLS = {
    "STR": "3d6",   # 力量
    "CON": "3d6",   # 体质
    "SIZ": "2d6",   # 体型 (2d6+6)*5
    "DEX": "3d6",   # 敏捷
    "APP": "3d6",   # 外貌
    "INT": "2d6",   # 智力 (2d6+6)*5
    "POW": "3d6",   # 意志
    "EDU": "2d6",   # 教育 (2d6+6)*5
}

# 2d6+6 的属性列表
PLUS_SIX_ATTRS = {"SIZ", "INT", "EDU"}

# ── 技能表（数据表 skills；官方数据见 data/skills.yaml）────────────────────


class SkillRow(BaseModel):
    """技能表的一行：技能名与起始值。母语与闪避由属性派生，表里的 0 只是占位。"""

    name: str = Field(min_length=1, title="技能")
    base: int = Field(0, ge=0, le=99, title="起始值")


SKILLS = DataTable(
    name="skills", title="技能表", row_model=SkillRow, key="name",
    source=Path(__file__).parent / "data" / "skills.yaml",
    description="技能起始值：建卡、检定（角色卡上没有这项技能时按起始值算）共用",
    build=lambda rows: frozen_mapping(rows, "name", "base"),
    brief=lambda r: f"起始值 {r['base']}",
)

#: 官方技能表（技能名 → 起始值，只读）。结算请经 :func:`default_skills` 取本局的表。
COC_DEFAULT_SKILLS = SKILLS.official


def default_skills(options: RuleConfig | None = None) -> Mapping[str, int]:
    """本局的技能表（官方 + 模组 + 村规补丁）。"""
    return table_of(options, "skills", COC_DEFAULT_SKILLS)


def roll_attributes() -> dict[str, int]:
    """掷一组 CoC 基础属性"""
    attrs = {}
    for name, notation in ATTRIBUTE_ROLLS.items():
        result = roll(notation)
        if name in PLUS_SIX_ATTRS:
            attrs[name] = (result.total + 6) * 5
        else:
            attrs[name] = result.total * 5
    return attrs


# ── 年龄修正（CoC 7e 建卡第 3 步）────────────────────────────────────────
#
# 这是 7 版建卡里最有味道的一步：年龄不是标签，是取舍——老角色学识深厚但体能衰退。
# 缺了它，70 岁的教授和 25 岁的运动员除了移动力毫无差别，年龄字段等于装饰。
#
# 每档给 (EDU 增强检定次数, 体能减值总额, APP 减值)。体能减值按规则由玩家在
# STR/CON/DEX 间自行分配，这里按「尽量平均、余数从 STR 起依次多扣」自动摊派，
# 保证总额与规则一致；玩家想换分配方式可在角色卡编辑页改。
_AGE_BANDS: tuple[tuple[int, int, int, int], ...] = (
    # (年龄上限, EDU 增强次数, STR/CON/DEX 合计减值, APP 减值)
    (19, 0, 0, 0),      # 15-19 另有 EDU-5、STR/SIZ-5、幸运两次取高，见下
    (39, 1, 0, 0),
    (49, 2, 5, 5),
    (59, 3, 10, 10),
    (69, 4, 20, 15),
    (79, 4, 40, 20),
    (89, 4, 80, 25),
)
_PHYSICAL_KEYS = ("STR", "CON", "DEX")
#: 属性下限：减值再狠也不该把人扣成负数或 0（0 在 CoC 里意味着已经不是活人了）。
_ATTR_FLOOR = 1
EDU_MAX = 99


def _band(age: int) -> tuple[int, int, int]:
    for ceiling, edu_rolls, physical, app_penalty in _AGE_BANDS:
        if age <= ceiling:
            return edu_rolls, physical, app_penalty
    return _AGE_BANDS[-1][1:]          # 90+ 沿用 80-89 档


def _spread(total: int, keys: tuple[str, ...]) -> dict[str, int]:
    """把减值总额摊到几个属性上：尽量平均，余数从头依次多扣一点。"""
    if total <= 0:
        return {}
    base, rest = divmod(total, len(keys))
    return {k: base + (1 if i < rest else 0) for i, k in enumerate(keys)}


def apply_age_modifiers(
    attrs: dict[str, int], age: int, *, roller=None,
) -> tuple[dict[str, int], list[dict]]:
    """按年龄修正属性，返回 (新属性, 明细)。

    明细每条形如 ``{"label", "detail", "delta"}``，供界面把「为什么变了」摊开给玩家看——
    建卡时属性被悄悄改掉是最容易让人觉得系统在乱来的地方。

    ``roller`` 仅供测试注入（签名同 ``roll``）。
    """
    r = roller or roll
    out = dict(attrs)
    notes: list[dict] = []
    edu_rolls, physical, app_penalty = _band(age)

    def _dec(key: str, amount: int) -> int:
        """扣减并返回**实际**扣掉的量（受下限约束）。"""
        if amount <= 0:
            return 0
        before = out.get(key, 0)
        out[key] = max(_ATTR_FLOOR, before - amount)
        return before - out[key]

    if age <= 19:
        # 少年：教育尚浅、身体没长开，但运气特别好
        actual = _dec("EDU", 5)
        if actual:
            notes.append({"label": "未成年", "detail": "教育 −5", "delta": -actual})
        # STR 与 SIZ 合计 −5，同样按摊派处理
        for key, amount in _spread(5, ("STR", "SIZ")).items():
            got = _dec(key, amount)
            if got:
                notes.append({"label": "未成年", "detail": f"{key} −{got}", "delta": -got})
    else:
        for key, amount in _spread(physical, _PHYSICAL_KEYS).items():
            got = _dec(key, amount)
            if got:
                notes.append({"label": "年龄衰退", "detail": f"{key} −{got}", "delta": -got})
        got = _dec("APP", app_penalty)
        if got:
            notes.append({"label": "年龄衰退", "detail": f"APP −{got}", "delta": -got})

    # 教育增强检定：每次 D100 > 当前 EDU 则 EDU + 1D10，封顶 99。
    # 掷点写进明细——玩家看得见自己是怎么涨上去的，比凭空多几点可信得多。
    for i in range(edu_rolls):
        check = r("1d100").total
        cur = out.get("EDU", 0)
        if check > cur:
            gain = r("1d10").total
            after = min(EDU_MAX, cur + gain)
            real = after - cur
            out["EDU"] = after
            notes.append({
                "label": f"教育增强 {i + 1}",
                "detail": f"D100={check} > 教育{cur}，+{gain}" + ("（封顶 99）" if real < gain else ""),
                "delta": real,
            })
        else:
            notes.append({
                "label": f"教育增强 {i + 1}",
                "detail": f"D100={check} ≤ 教育{cur}，未提升",
                "delta": 0,
            })
    return out, notes


def roll_luck(age: int = 25, *, roller=None) -> tuple[int, list[int]]:
    """掷幸运（3D6×5）。15-19 岁掷两次取高——这是给少年角色的补偿。

    返回 (幸运值, 各次掷出的原始值)，第二个值供界面说明「两次取高」。
    """
    r = roller or roll
    rolls = [r("3d6").total * 5]
    if age <= 19:
        rolls.append(r("3d6").total * 5)
    return max(rolls), rolls


#: 伤害加值 / 体格表（按 STR+SIZ）。7 版在 164 以上每 80 点进一档，
#: 早先的实现最后一档是 else，165 往上全归 1D6——玩家角色很难超 204，但 NPC 与
#: 怪物会，而战斗引擎用的是同一套派生。
_DAMAGE_BONUS_TABLE: tuple[tuple[int, str, int], ...] = (
    (64, "-2", -2),
    (84, "-1", -1),
    (124, "0", 0),
    (164, "1d4", 1),
    (204, "1d6", 2),
    (284, "2d6", 3),
    (364, "3d6", 4),
    (444, "4d6", 5),
)


def damage_bonus(combined: int) -> tuple[str, int]:
    """按 STR+SIZ 取 (伤害加值骰式, 体格)。超出表尾时每 80 点续进一档。"""
    for ceiling, db, build in _DAMAGE_BONUS_TABLE:
        if combined <= ceiling:
            return db, build
    # 444 以上：每满 80 点多一个 D6（体格同步 +1），巨型怪物用得上
    extra = (combined - 445) // 80 + 1
    return f"{4 + extra}d6", 5 + extra


@hookimpl(trylast=True)
def max_hp(attrs: dict) -> int:
    """官方最大 HP：(CON+SIZ)/10。以 trylast 挂上，变体组件（如 Pulp）的实现排在它前面、先生效。"""
    return (attrs.get("CON", 50) + attrs.get("SIZ", 50)) // 10


def max_hp_of(attrs: dict[str, int], options: RuleConfig | None = None) -> int:
    """本局规则下的最大 HP（经钩子 ``max_hp``：变体可以换掉算法）。"""
    hp = hooks_of(options).max_hp(attrs=attrs, options=options)
    return hp if hp is not None else max_hp(attrs)


def compute_derived(attrs: dict[str, int], age: int = 25, options: RuleConfig | None = None) -> dict:
    """计算派生属性（最大 HP 经钩子，其余按规则书）"""
    str_val = attrs.get("STR", 50)
    siz_val = attrs.get("SIZ", 50)
    dex_val = attrs.get("DEX", 50)
    pow_val = attrs.get("POW", 50)

    hp = max_hp_of(attrs, options)
    mp = pow_val // 5
    san = pow_val
    # 走 roll_luck 而非裸 3d6：未成年「两次取高」是年龄规则的一部分，
    # 不能只在新建卡流程里生效、老路径悄悄漏掉。
    luck, _ = roll_luck(age)

    # 移动力：7 版三档。
    # 注意第二、三档的判据不同——「任一 ≥ SIZ」是 8，「两者都 > SIZ」才是 9。
    # 早先第二档写成 `dex >= siz or str >= siz`，把第三档的情况全吞了，MOV 永远算不出 9
    # （穷举 64 组属性验证过一次都没出现）。
    if str_val > siz_val and dex_val > siz_val:
        mov = 9
    elif str_val < siz_val and dex_val < siz_val:
        mov = 7
    else:
        mov = 8

    if age >= 80:
        mov -= 5
    elif age >= 70:
        mov -= 4
    elif age >= 60:
        mov -= 3
    elif age >= 50:
        mov -= 2
    elif age >= 40:
        mov -= 1

    db, build = damage_bonus(str_val + siz_val)

    return {
        "hitPoints": {"current": hp, "max": hp},
        "magicPoints": {"current": mp, "max": mp},
        "sanity": {"current": san, "max": 99},
        "luck": luck,
        "move": mov,
        "damageBonus": db,
        "build": build,
        "age": age,
        "occupation": "",
    }


def asset_tier(cr: int) -> str:
    """信用评级 → 财富等级。"""
    if cr <= 0:
        return "一贫如洗"
    if cr <= 9:
        return "贫穷"
    if cr <= 49:
        return "普通"
    if cr <= 89:
        return "富裕"
    if cr <= 98:
        return "富有"
    return "巨富"


def derive_assets(cr: int) -> dict:
    """信用评级 → 消费水平 / 现金 / 资产（7 版标准表，1920s 美元）。

    与前端 `apps/web/src/components/character/useCocData.ts` 的 `deriveAssets` 是同一张表，
    两边各有一份是因为编辑器里的「按信用评级换算」按钮要即时出数、不值得为一张查找表走
    一次往返。**改一边必须改另一边**：两侧都有把具体数值钉死的用例（本仓 tests 与
    useCocData.test.ts），任一侧漂移都会红。

    现金那一列一度比规则书高一个量级（×2/×20/×50/×100），建出来的调查员一上来就揣着
    十倍的钱；前端已经修过，这里从一开始就按修正后的倍率写。
    """
    tier = asset_tier(cr)
    if cr <= 0:
        return {"tier": tier, "spendingLevel": 0.5, "cash": 0.5, "assets": 0}
    if cr <= 9:
        return {"tier": tier, "spendingLevel": 2, "cash": cr * 1, "assets": cr * 10}
    if cr <= 49:
        return {"tier": tier, "spendingLevel": 10, "cash": cr * 2, "assets": cr * 50}
    if cr <= 89:
        return {"tier": tier, "spendingLevel": 50, "cash": cr * 5, "assets": cr * 500}
    if cr <= 98:
        return {"tier": tier, "spendingLevel": 250, "cash": cr * 20, "assets": cr * 2000}
    return {"tier": tier, "spendingLevel": 5000, "cash": 50000, "assets": 5000000}


def apply_attr_derived_skills(
    skills: dict[str, int], attrs: dict[str, int]
) -> dict[str, int]:
    """确保属性派生技能达到其基础底线：母语≥EDU、闪避≥DEX//2。

    母语/闪避依赖具体角色属性，静态的 COC_DEFAULT_SKILLS 无法预置正确值。
    无论客户端是否自带 skills，都在此兜底，避免出现 0/缺失；取 max 以保留
    玩家在基线之上的合法加点。原地修改并返回 skills。
    """
    skills["母语"] = max(skills.get("母语", 0), attrs.get("EDU", 50))
    skills["闪避"] = max(skills.get("闪避", 0), attrs.get("DEX", 50) // 2)
    return skills


def build_default_skills(attrs: dict[str, int], options: RuleConfig | None = None) -> dict[str, int]:
    """基于属性构建默认技能列表（本局技能表的全部技能 + 母语/闪避派生值）"""
    return apply_attr_derived_skills(dict(default_skills(options)), attrs)


# ── 建卡入口（原 CoCRuleEngine）────────────────────────────────────────

ATTRIBUTE_SCHEMA = [
    {"key": "STR", "name": "力量", "roll": "3d6×5"},
    {"key": "CON", "name": "体质", "roll": "3d6×5"},
    {"key": "SIZ", "name": "体型", "roll": "(2d6+6)×5"},
    {"key": "DEX", "name": "敏捷", "roll": "3d6×5"},
    {"key": "APP", "name": "外貌", "roll": "3d6×5"},
    {"key": "INT", "name": "智力", "roll": "(2d6+6)×5"},
    {"key": "POW", "name": "意志", "roll": "3d6×5"},
    {"key": "EDU", "name": "教育", "roll": "(2d6+6)×5"},
]


def get_character_schema(options: RuleConfig | None = None) -> dict:
    """角色卡字段定义。注意：母语/闪避在 default_skills 里是占位的 0——它们由属性派生，
    静态表填不了真值；要拿「这组属性下的真实起始值」请走 :func:`base_skills`。"""
    return {
        "rule_system": "coc",
        "attributes": ATTRIBUTE_SCHEMA,
        "derived": ["HP", "MP", "SAN", "MOV", "伤害加值", "体格", "幸运"],
        "system_specific_fields": ["sanity", "luck", "age", "occupation"],
        "default_skills": dict(default_skills(options)),
    }


def base_skills(attrs: dict[str, int], options: RuleConfig | None = None) -> dict[str, int]:
    """这组属性下、**未加任何点**时的技能起始值：静态表 + 母语(=EDU)、闪避(=DEX//2)。

    建卡界面必须拿这一份而不是 schema 里的 default_skills：后者的母语/闪避是占位的 0，
    界面照着 0 展示，玩家为了把它填上去而花掉的点，落库时会被兜底的
    ``max(提交值, 派生值)`` 顶掉，凭空蒸发。
    """
    return build_default_skills(attrs, options)


def create_character(data: dict, options: RuleConfig | None = None) -> dict:
    """根据输入创建角色，自动计算派生属性。"""
    attrs = data.get("base_attributes", {})
    if not attrs:
        attrs = roll_attributes()

    age = data.get("age", 25)
    system_data = compute_derived(attrs, age, options)

    extra_sd = data.get("system_data", {})
    if isinstance(extra_sd, dict):
        for k, v in extra_sd.items():
            if v is not None and v != "":
                system_data[k] = v

    skills = data.get("skills")
    if not skills:
        skills = build_default_skills(attrs, options)
    else:
        # 客户端自带 skills（如前端加点）时，静态默认表缺少母语/闪避的属性
        # 派生值，这里兜底补齐，避免绕过 build_default_skills 导致两者为 0
        skills = apply_attr_derived_skills(dict(skills), attrs)

    return {
        "base_attributes": attrs,
        "skills": skills,
        "system_data": system_data,
    }


def validate_character(character_data: dict) -> tuple[bool, list[str]]:
    """任何角色卡都要满足的基本范围（导入的老卡、团中成长过的卡也一样）。

    建卡规则（点数、上限、职业）不在这里——那由 ``rs.creation.validate_build`` 按建卡记录校验，
    只管新建的卡。这里曾按 15–90 卡属性，可年龄修正能把教育推到 99、把体能压到 15 以下，
    高龄或高教育的合法新卡反被拒。
    """
    errors = []
    attrs = character_data.get("base_attributes", {})
    required = ["STR", "CON", "SIZ", "DEX", "APP", "INT", "POW", "EDU"]

    for attr in required:
        val = attrs.get(attr)
        if val is None:
            errors.append(f"缺少属性: {attr}")
        elif not (_ATTR_FLOOR <= val <= 99):
            errors.append(f"{attr} 值 {val} 超出范围 ({_ATTR_FLOOR}-99)")
    for name, val in (character_data.get("skills") or {}).items():
        if not isinstance(val, int) or not 0 <= val <= 99:
            errors.append(f"技能 {name} 值 {val} 超出范围 (0-99)")

    return (len(errors) == 0, errors)


def roll_attribute_sets(count: int = 3) -> list[dict[str, int]]:
    """掷多组属性供玩家选择。"""
    return [roll_attributes() for _ in range(count)]


COMPONENT = Component(
    id="coc7.character", title="属性与建卡",
    requires=("coc7.dice",),
    data=(SKILLS,),
    hooks=__name__,
    provides={"character": Provide(
        __name__,
        bound=(
            "default_skills", "build_default_skills", "get_character_schema", "base_skills",
            "create_character", "compute_derived", "max_hp_of",
        ),
        plain=(
            "ATTRIBUTE_ROLLS", "roll_attributes", "apply_age_modifiers",
            "roll_luck", "damage_bonus", "derive_assets",
            "apply_attr_derived_skills", "validate_character", "roll_attribute_sets",
        ),
    )},
)
