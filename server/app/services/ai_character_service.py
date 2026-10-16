"""AI 自动生成 CoC 七版角色卡

设计原则：规则引擎保证合法（掷属性、算点数、技能钳制），LLM 保证贴合模组
与有趣（选职业、技能加点意图、姓名/背景）。返回结构与
``excel_import.parse_coc_character_sheet`` 对齐，供前端复用同一套填表逻辑。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlalchemy.orm import Session

from app.ai.llm_factory import get_llm
from app.models.module import Module
from app.rules import Ruleset, ruleset_for
from app.services import rule_options_service

logger = logging.getLogger(__name__)


def _raw() -> Ruleset:
    """没给生效规则时（单测直接调内部函数）按 CoC 规则书原文。"""
    return ruleset_for("coc")

# 建卡时不可分配的技能：克苏鲁神话恒为 0；母语 = EDU，由引擎设置
#: 真正不可加点的只有克苏鲁神话。母语曾经也在这里，但它的特殊之处是**基础值等于 EDU**，
#: 不是「不能加」——本仓自己的职业表里，母语就列在会计/作家/律师/记者等职业的本职技能里，
#: 本职技能表的含义正是「这些可以花本职点加」。锁掉它等于和自己的数据打架，也和手动
#: 建卡那条路（只锁克苏鲁神话）对不上。
NON_ALLOCATABLE = {"克苏鲁神话"}

BACKSTORY_LABELS = {
    "personalDescription": "个人描述",
    "ideologyBeliefs": "思想/信念",
    "significantPeople": "重要之人",
    "meaningfulLocations": "意义非凡之地",
    "treasuredPossessions": "宝贵之物",
    "traits": "特点",
}

# LLM 不可用 / 非法职业时的兜底职业
DEFAULT_OCCUPATION_BY_ERA = {
    "1920s": "记者",
    "现代": "记者",
    "modern": "记者",
}


# --------------------------------------------------------------------------- #
# 公开入口
# --------------------------------------------------------------------------- #
async def generate_ai_character(
    db: Session, module_id: str, hint: str = "", is_player: bool = False,
) -> dict[str, Any]:
    """生成一张完整、规则合法的角色卡草稿（不落库）。

    返回与 excel_import 一致的扁平结构：
    name / age / base_attributes / skills / system_data / backstory。
    is_player 暂为后续阶段（AI 队友直接落库）预留，阶段 0 前端走正常创建流程。
    """
    module = db.get(Module, module_id)
    if not module:
        raise ValueError("模组不存在")

    # 按这一桌的村规与该模组自带的数据建卡：房主新增的职业、技能要能被 AI 选到
    rs = rule_options_service.village_ruleset(db, module.rule_system, module)
    attrs = rs.character.roll_attributes()

    ai: dict | None = None
    try:
        llm = get_llm()
        raw = await llm.complete(
            messages=[{"role": "user", "content": _build_prompt(module, hint, attrs, rs=rs)}],
            response_format={"type": "json_object"},
            temperature=0.8,
        )
        ai = _parse_json(raw)
    except Exception:
        logger.exception("AI 建卡 LLM 调用失败: module=%s", module_id)

    if ai is None:
        return _rule_only_fallback(module, attrs, rs=rs)

    return _assemble(module, attrs, ai, rs=rs)


# --------------------------------------------------------------------------- #
# Prompt 构造
# --------------------------------------------------------------------------- #
def _build_prompt(module: Module, hint: str, attrs: dict[str, int], *, rs: Ruleset | None = None) -> str:
    rs = rs or _raw()
    era = (module.world_setting or {}).get("era", "1920s")
    occ_names = "、".join(o.name for o in rs.occupation.occupations())
    skill_names = "、".join(k for k in rs.character.default_skills() if k not in NON_ALLOCATABLE)
    attrs_str = "、".join(f"{k}={v}" for k, v in attrs.items())
    hint_line = f"\n## 角色概念提示\n{hint}\n" if hint and hint.strip() else ""

    return f"""你是一位资深的克苏鲁的呼唤（CoC）七版守秘人，需要为玩家快速生成一张贴合模组背景的调查员角色卡。

## 模组信息
- 标题：{module.title}
- 年代：{era}
- 简介：{module.description or "（无）"}

## 已为该角色掷好的属性（请勿改动，仅作为设计参考）
{attrs_str}
{hint_line}
## 你的任务
基于以上属性与模组背景，设计一个**贴合 {era} 年代**、有血有肉的调查员：
1. occupation：从下列职业中选一个最合适的，必须原样返回其中之一：
{occ_names}
2. skills：列出 8-15 个你想加点的技能及其**目标总值**（整数 1-90）。技能名必须来自下列名单，禁止虚构、禁止带括号子项以外的写法：
{skill_names}
3. credit_rating：信用评级整数（会被钳制到所选职业的合理范围）
4. name / age（15-89，且与设定相符）/ gender
5. residence / birthplace：居住地与出生地，各写一个具体地名（贴合模组的地域与 {era} 年代；
   两者可以相同，但更常见的是「生于某地、现居案发城市」）
6. 六段结构化背景，各 1-2 句中文：personalDescription、ideologyBeliefs、significantPeople、meaningfulLocations、treasuredPossessions、traits
7. equipment：5-10 件贴合职业与 {era} 年代的随身物品名（字符串数组，物品须符合年代，不要武器以外的现代物品）

注意：{era} 年代不应出现不符合时代的科技、概念或物品。

## 返回格式（严格 JSON，不要任何额外文字或解释）
{{"name":"","age":25,"gender":"","residence":"","birthplace":"","occupation":"","credit_rating":0,"skills":{{"侦查":60,"聆听":50}},"equipment":["笔记本","怀表"],"personalDescription":"","ideologyBeliefs":"","significantPeople":"","meaningfulLocations":"","treasuredPossessions":"","traits":"","backstory":""}}"""


# --------------------------------------------------------------------------- #
# JSON 解析（三级容错）
# --------------------------------------------------------------------------- #
def _parse_json(raw: Any) -> dict | None:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", raw, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None


# --------------------------------------------------------------------------- #
# 组装 + 技能钳制（合法性保证全在这里）
# --------------------------------------------------------------------------- #
def _assemble(
    module: Module, attrs: dict[str, int], ai: dict, *, rs: Ruleset | None = None,
) -> dict[str, Any]:
    rs = rs or _raw()
    age = _clamp_int(ai.get("age"), 15, 89, 25)

    occ = rs.occupation.get_occupation(str(ai.get("occupation", "")))
    if occ is None:
        occ = _default_occupation(module, rs=rs)
    occ_name = occ.name

    wanted: dict[str, int] = {}
    ai_skills_raw = ai.get("skills")
    for k, v in (ai_skills_raw.items() if isinstance(ai_skills_raw, dict) else ()):
        try:
            wanted[str(k)] = int(v)
        except (ValueError, TypeError):
            continue
    cr_wanted = _clamp_int(ai.get("credit_rating"), occ.credit_min, occ.credit_max, occ.credit_min)
    final_attrs, final, cr, creation = _rule_build(rs, attrs, age, occ, wanted, cr_wanted)

    return _build_result(final_attrs, age, occ_name, cr, final, ai, rs=rs, creation=creation)


def _build_result(
    attrs: dict[str, int], age: int, occ_name: str, cr: int,
    skills: dict[str, int], ai: dict, *, rs: Ruleset | None = None, creation: dict | None = None,
) -> dict[str, Any]:
    rs = rs or _raw()
    system_data = rs.character.compute_derived(attrs, age)
    system_data["occupation"] = occ_name
    system_data["creditRating"] = cr
    _fill_assets(system_data, cr, rs=rs)
    for key in ("gender", "residence", "birthplace"):
        val = str(ai.get(key) or "").strip()
        if val:
            system_data[key] = val

    backstory_parts: list[str] = []
    for key, label in BACKSTORY_LABELS.items():
        val = str(ai.get(key) or "").strip()
        if val:
            system_data[key] = val
            backstory_parts.append(f"【{label}】{val}")
    free = str(ai.get("backstory") or "").strip()
    if free:
        backstory_parts.append(free)

    eq_raw = ai.get("equipment")
    equipment = (
        [str(e).strip() for e in eq_raw if str(e).strip()][:12]
        if isinstance(eq_raw, list) else []
    )
    if equipment:
        system_data["equipment"] = equipment

    return {
        "name": str(ai.get("name") or "无名调查员").strip(),
        "age": age,
        "base_attributes": attrs,
        "skills": skills,
        "system_data": system_data,
        "backstory": "\n".join(backstory_parts),
        "equipment": equipment,
        # 建卡记录：前端据此把这张卡当成一次正常车卡载入（清单、点数照常算），创建时后端再校验
        "creation": creation,
    }


def _fill_assets(system_data: dict[str, Any], cr: int, *, rs: Ruleset | None = None) -> None:
    rs = rs or _raw()
    """按信用评级把现金/消费水平/资产算进 system_data。

    这一步此前完全缺席：AI 建出来的卡只有一个 creditRating，现金与资产是空的，
    要玩家自己去编辑器里点一次「按信用评级换算」才补上——可那正是一步纯查表，
    没有任何需要人来决定的东西。字段名与前端编辑器读写的一致（见 readAssets）。
    """
    d = rs.character.derive_assets(cr)
    system_data["cash"] = d["cash"]
    system_data["spendingLevel"] = d["spendingLevel"]
    # assets 在角色卡上是一行自由描述；换算给出的是金额，与编辑器那颗按钮同样的写法
    system_data["assets"] = f"约 ${d['assets']:,}"


# --------------------------------------------------------------------------- #
# 兜底：LLM 完全不可用时，用纯规则产出一张合法卡
# --------------------------------------------------------------------------- #
def _rule_only_fallback(
    module: Module, attrs: dict[str, int], *, rs: Ruleset | None = None,
) -> dict[str, Any]:
    rs = rs or _raw()
    occ = _default_occupation(module, rs=rs)
    final_attrs, base, cr, creation = _rule_build(rs, attrs, 25, occ, {}, occ.credit_min)

    system_data = rs.character.compute_derived(final_attrs, 25)
    system_data["occupation"] = occ.name
    system_data["creditRating"] = cr
    _fill_assets(system_data, cr, rs=rs)

    return {
        "name": "待命名调查员",
        "age": 25,
        "base_attributes": final_attrs,
        "skills": base,
        "system_data": system_data,
        "backstory": "",
        "creation": creation,
        "_fallback": True,
    }


# --------------------------------------------------------------------------- #
# 按建卡规则把「掷好的属性 + 年龄 + 职业 + 想要的技能值」落成一张合法卡
# --------------------------------------------------------------------------- #
def _rule_build(
    rs: Ruleset, base_attrs: dict[str, int], age: int, occ: Any, wanted: dict[str, int], cr: int,
) -> tuple[dict[str, int], dict[str, int], int, dict | None]:
    """返回 (修正后属性, 技能, 信用评级, 建卡记录)。

    与手动车卡同一套规则：年龄修正（扣点按规则摊派，记进建卡记录）、选择组与「任意 N 项」的勾选、
    职业点只加本职、兴趣点只加其余、本桌建卡上限。最后用 ``rs.creation.validate_build`` 自检，
    万一不过就不给建卡记录（前端会按现成卡处理），并记日志——不该发生。
    """
    # 教育增强与外貌减值照规则书掷 / 扣；体能扣点自己分：先平摊，扣到底（1）的那项扣不动的部分
    # 挪给其余几项——直接用 apply_age_modifiers 的平摊，属性低时总额会扣不够，过不了校验
    final_attrs, _notes = rs.character.apply_age_modifiers(dict(base_attrs), age)
    band = rs.creation.age_band(age)
    keys = list(band["deduct_from"])
    need = rs.creation.required_deduction(age, base_attrs)
    deductions = {k: 0 for k in keys}
    while need > 0:
        room = [k for k in keys if base_attrs[k] - 1 - deductions[k] > 0]
        share = max(1, need // len(room))
        for k in room:
            take = min(share, base_attrs[k] - 1 - deductions[k], need)
            deductions[k] += take
            need -= take
            if need == 0:
                break
    deductions = {k: v for k, v in deductions.items() if v}
    for k in keys:
        final_attrs[k] = base_attrs[k] - deductions.get(k, 0)

    occ_mod = rs.occupation
    start = rs.character.build_default_skills(final_attrs)
    # 能加点的技能（母语、闪避也能在起始值之上加；只有克苏鲁神话锁死，信用评级单算）
    pickable = [k for k in start if k not in NON_ALLOCATABLE and k != "信用评级"]
    ranked = sorted(pickable, key=lambda k: -wanted.get(k, -1))   # AI 想加的排前面，其余保持表序

    picks: list[str] = []
    for gi, g in enumerate(occ.groups):
        cands = [k for k in ranked if occ_mod.occupation_skill_role(occ, k) == f"group:{gi}"]
        picks += cands[: g.pick]
    free = [k for k in ranked if occ_mod.occupation_skill_role(occ, k) == "free" and k not in picks]
    picks += free[: occ.choices]

    is_occ = lambda k: occ_mod.is_occupation_skill(occ, picks, k)  # noqa: E731
    occ_points = occ_mod.calc_occupation_points(occ.name, final_attrs)
    int_points = occ_mod.calc_interest_points(final_attrs)
    cr = max(occ.credit_min, min(cr, occ.credit_max, occ_points))
    opts = rs.options
    cap = lambda k: opts.skill_cap_occupation if is_occ(k) else opts.skill_cap_other  # noqa: E731

    occ_targets = [k for k in pickable if is_occ(k)]
    occ_delta: dict[str, int] = {}
    int_delta: dict[str, int] = {}
    if wanted:
        for k in pickable:
            if k not in wanted:
                continue
            target = min(wanted[k], max(cap(k), start[k]))
            d = max(0, target - start[k])
            if d:
                (occ_delta if is_occ(k) else int_delta)[k] = d
    elif occ_targets:
        # 没有 AI 的意向（兜底）：职业点平摊到本职技能上
        per = (occ_points - cr) // len(occ_targets)
        for k in occ_targets:
            occ_delta[k] = max(0, min(per, max(cap(k), start[k]) - start[k]))
    occ_delta = _scale_to_budget(occ_delta, max(0, occ_points - cr))
    int_delta = _scale_to_budget(int_delta, int_points)

    skills = dict(start)
    for k, d in {**occ_delta, **int_delta}.items():
        skills[k] = start[k] + d
    skills["信用评级"] = cr

    creation = {
        "method": "roll", "base_attributes": dict(base_attrs), "age": age,
        "age_deductions": deductions, "occupation": occ.name, "occupation_picks": picks,
    }
    problems = rs.creation.validate_build(creation, final_attrs, skills)
    if problems:
        logger.warning("AI 建卡未通过建卡规则自检，按现成卡返回：%s", "；".join(problems))
        creation = None
    return final_attrs, skills, cr, creation


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def _scale_to_budget(deltas: dict[str, int], budget: int) -> dict[str, int]:
    """把一组技能增量按比例缩放到预算内，向下取整保证不超。"""
    total = sum(deltas.values())
    if total <= budget or total == 0:
        return deltas
    f = budget / total
    return {k: int(v * f) for k, v in deltas.items()}


def _clamp_int(val: Any, lo: int, hi: int, default: int) -> int:
    try:
        v = int(val)
    except (ValueError, TypeError):
        return default
    return max(lo, min(hi, v))


def _default_occupation(module: Module, *, rs: Ruleset | None = None) -> Any:  # 职业条目
    rs = rs or _raw()
    era = (module.world_setting or {}).get("era", "1920s")
    occ = rs.occupation.get_occupation(DEFAULT_OCCUPATION_BY_ERA.get(era, "记者"))
    return occ or rs.occupation.occupations()[0]
