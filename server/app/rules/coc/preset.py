"""CoC 7e 官方规则：组件目录与官方预设。

每个组件只是**声明**：它把哪些模块函数/数据表挂到哪个服务槽位、依赖谁、读哪些村规键。
算法本身仍在各模块里，这一层不改任何结算行为（P1：原样包装，见设计文档第 10 节）。

服务槽位一览（业务层用 ``rs.<槽位>.<导出>`` 访问）：

======== =============================== ==========================
槽位       内容                              提供组件
======== =============================== ==========================
dice       骰式与 d100                       coc7.dice
sheet      角色卡 schema、建卡、校验            coc7.character
character  属性、年龄修正、派生值、技能起始值     coc7.character
occupation 职业表与技能点                     coc7.occupation
specialization 技能专精表                     coc7.occupation
equipment  装备表                            coc7.equipment
check      技能检定、达成等级                   coc7.check
sanity     SAN 检定、临时疯狂口径               coc7.sanity
madness    疯狂发作、症状对检定的惩罚            coc7.madness
wound      伤害结算、重伤、濒死                 coc7.wound
status     角色状态表与严重度                   coc7.wound
combat     先攻、攻击、闪避、急救、NPC 行动       coc7.combat
weapons    武器表                            coc7.combat
positioning 方格、射程、视线、掩体               coc7.positioning
chase      追逐轮                            coc7.chase
growth     成长检定                           coc7.growth
luck       幸运消费与燃运重骰                   coc7.luck
======== =============================== ==========================
"""

from __future__ import annotations

from dataclasses import fields

from app.rules import dice
from app.rules.coc import (
    character,
    chase,
    checks,
    combat,
    equipment,
    luck,
    madness,
    occupations,
    options,
    positioning,
    sanity,
    specializations,
    status,
    weapons,
)
from app.rules.coc.engine import CoCRuleEngine
from app.rules.kernel import Component, Provide, RuleSystem


def build_system(engine: CoCRuleEngine) -> RuleSystem:
    """``engine`` 传入已注册的那个实例：``sheet``/``growth`` 槽位指向它本身。"""
    components = (
        Component(
            id="coc7.dice", title="骰子",
            provides={"dice": Provide(dice, plain=("roll", "roll_percentile"))},
        ),
        Component(
            id="coc7.character", title="属性与建卡",
            requires=("coc7.dice",),
            provides={
                "sheet": Provide(engine, plain=(
                    "get_character_schema", "base_skills", "create_character",
                    "validate_character", "roll_attribute_sets",
                )),
                "character": Provide(character, plain=(
                    "ATTRIBUTE_ROLLS", "COC_DEFAULT_SKILLS",
                    "roll_attributes", "apply_age_modifiers", "roll_luck", "damage_bonus",
                    "compute_derived", "derive_assets", "build_default_skills",
                    "apply_attr_derived_skills",
                )),
            },
        ),
        Component(
            id="coc7.occupation", title="职业与专精",
            requires=("coc7.character",),
            provides={
                "occupation": Provide(occupations, plain=(
                    "COC_OCCUPATIONS", "OCCUPATION_CATEGORY", "OCCUPATION_CATEGORY_ORDER",
                    "get_occupation", "calc_occupation_points", "calc_interest_points",
                )),
                "specialization": Provide(specializations, plain=("SPECIALIZATIONS", "SINGLE_SPEC")),
            },
        ),
        Component(
            id="coc7.equipment", title="装备",
            requires=("coc7.character",),
            provides={"equipment": Provide(
                equipment, plain=("COC_EQUIPMENT", "get_available_equipment"),
            )},
        ),
        Component(
            id="coc7.check", title="技能检定",
            requires=("coc7.dice", "coc7.character"),
            option_keys=("critical_max", "fumble_rule", "dice_pool_cap"),
            provides={"check": Provide(
                checks,
                bound=("resolve_skill_check", "is_fumble", "achieved_tier", "judge"),
                plain=(
                    "TIER_LABEL_CN", "display_skill_name", "normalize_skill_name",
                    "resolve_skill_value",
                ),
            )},
        ),
        Component(
            id="coc7.sanity", title="理智",
            requires=("coc7.check",),
            option_keys=("insanity_rule", "insanity_flat_threshold"),
            provides={"sanity": Provide(sanity, bound=("san_check", "insanity_trigger_text"))},
        ),
        Component(
            id="coc7.madness", title="疯狂发作",
            requires=("coc7.sanity",),
            provides={"madness": Provide(madness, plain=(
                "BOUT_SYMPTOMS", "make_bout", "roll_symptom", "roll_duration", "check_penalty",
            ))},
        ),
        Component(
            id="coc7.wound", title="伤害与状态",
            requires=("coc7.check",),
            option_keys=("major_wound_divisor",),
            provides={
                "wound": Provide(combat, bound=("is_major_wound", "resolve_wound", "tick_dying")),
                "status": Provide(status, plain=(
                    "CHARACTER_STATUSES", "STATUS_SEVERITY", "VALID_STATUS_VALUES",
                    "LEGACY_STATUS_ALIAS",
                )),
            },
        ),
        Component(
            id="coc7.combat", title="战斗轮",
            requires=("coc7.check", "coc7.wound"),
            provides={
                "combat": Provide(
                    combat,
                    bound=("resolve_attack", "resolve_first_aid", "resolve_observe", "resolve_maneuver"),
                    plain=(
                        "OUTCOME_RANK", "lookup_weapon", "resolve_weapon", "burst_capacity",
                        "roll_weapon_damage", "combat_skill_value", "roll_initiative",
                        "compare_checks", "attack_skill_of", "allowed_reactions", "is_active",
                        "check_combat_end", "advance_turn", "heuristic_defense",
                        "heuristic_npc_action",
                    ),
                ),
                "weapons": Provide(weapons, plain=("COC_WEAPONS", "WEAPON_CATEGORY_ORDER")),
            },
        ),
        Component(
            id="coc7.positioning", title="方格站位",
            requires=("coc7.combat",),
            provides={"positioning": Provide(positioning, plain=(
                "cell_distance", "is_adjacent", "range_in_cells", "range_check",
                "has_line_of_sight", "cover_penalty", "point_blank_bonus", "flank_penalty",
                "sweep_targets", "default_deployment", "step_toward", "reachable_cells",
            ))},
        ),
        Component(
            id="coc7.chase", title="追逐",
            requires=("coc7.check",),
            provides={"chase": Provide(
                chase, bound=("resolve_chase_round",), plain=("check_chase_end",),
            )},
        ),
        Component(
            id="coc7.growth", title="技能成长",
            requires=("coc7.check",),
            option_keys=("improvement",),
            provides={"growth": Provide(engine, bound=("improvement_check",))},
        ),
        # 设计稿把幸运拆成 luck-spend / luck-reroll 两个可选组件；P1 两者共用 luck 模块的
        # 一组函数，先合成一个组件，开关仍由村规布尔项控制。拆分随 P2「组件开关」一起做。
        Component(
            id="coc7.luck", title="幸运消费与燃运重骰",
            requires=("coc7.check",),
            option_keys=(
                "luck_spend", "luck_spend_max", "luck_spend_in_combat",
                "luck_spend_blocks_improvement", "luck_reroll", "luck_reroll_cost",
            ),
            provides={"luck": Provide(
                luck,
                bound=("rescue_offer", "apply_rescue", "apply_reroll"),
                plain=("FORBIDDEN_SKILLS", "available_luck", "is_forbidden_skill", "deduct"),
            )},
        ),
    )
    return RuleSystem(
        id="coc",
        components=components,
        preset=tuple(c.id for c in components),
        option_fields=tuple(f.name for f in fields(options.CocRuleOptions)),
        parse_options=options.from_dict,
        merge_options=options.merge,
        describe_options=options.describe,
    )
