"""coc7.wound：伤害与状态——重伤、濒死、昏迷、即死，以及角色状态表。

战斗结算（combat_service）与叙事扣血（turn_effects 的 HP_CHANGE）共用这里的口径。
槽位 ``rs.wound``。村规参数见 :class:`WoundConfig`；未传村规时按其默认值（规则书原文）。
"""

from __future__ import annotations

from app.rules.coc.components.check import resolve_skill_check
from app.rules.config import RuleConfig, rule_field
from app.rules.hooks import hooks_of, merge_effects
from app.rules.kernel import Component, Provide

# ── 角色状态（键 + 中文标签）──────────────────────────────────────────────
# 键为稳定英文标识，落库与代码判断用；标签供前端展示。
# 重伤/昏迷来自战斗与生命值；临时/不定期/永久疯狂来自理智系统。

CHARACTER_STATUSES: list[dict[str, str]] = [
    {"value": "active", "label": "正常"},
    {"value": "major_wound", "label": "重伤"},
    {"value": "unconscious", "label": "昏迷"},
    {"value": "dead", "label": "死亡"},
    {"value": "temporary_insanity", "label": "临时疯狂"},
    {"value": "indefinite_insanity", "label": "不定期疯狂"},
    {"value": "permanent_insanity", "label": "永久疯狂"},
]

# 历史遗留值 → 新值（incapacitated 旧义≈重伤）
LEGACY_STATUS_ALIAS = {"incapacitated": "major_wound"}

VALID_STATUS_VALUES = {s["value"] for s in CHARACTER_STATUSES}

#: 状态严重度：落疯狂等新状态时只升不降——疯狂不覆盖更严重的既有状态（昏迷/死亡），
#: 永久疯狂也不被临时疯狂降级。
STATUS_SEVERITY: dict[str, int] = {
    "active": 0, "major_wound": 1, "temporary_insanity": 2,
    "indefinite_insanity": 3, "unconscious": 4, "permanent_insanity": 5, "dead": 6,
}


class WoundConfig(RuleConfig):
    """coc7.wound：伤害与重伤。"""

    major_wound_divisor: int = rule_field(
        2, ge=1, le=10, title="重伤阈值",
        hint="单击伤害 ≥ 最大 HP ÷ {value} 判重伤（规则书是 2，即半血）",
    )

    def major_wound_text(self) -> str:
        return "最大 HP 的一半" if self.major_wound_divisor == 2 else f"最大 HP 的 1/{self.major_wound_divisor}"

    def manual_lines(self) -> list[str]:
        """KP 手册里的伤害规则（与 combat.resolve_wound 的分支逐条对应）。"""
        return [
            f"单次伤害 ≥ {self.major_wound_text()} = 重伤：**系统在结算 HP_CHANGE 时自动过体质检定、"
            "失败自动落昏迷**——你不要再为此补发 [DICE_CHECK]，只需根据系统给出的检定结果续写状态"
            "（昏迷者无法行动与感知）。",
            "HP 归 0 时看伤势：受过重伤（这一击就是重伤，或此前已带重伤）→ 濒死，需急救/医学稳定，"
            "否则每轮体质检定失败即死；只受轻伤归 0 → 昏迷倒地，伤势稳定、不致死。",
            "单次伤害超过最大 HP = 当场死亡。",
        ]

    def describe(self) -> list[str]:
        if self.major_wound_divisor == 2:
            return []
        return [
            f"重伤线：单次伤害达到最大 HP 的 1/{self.major_wound_divisor} 即为重伤"
            "（原文是半血），描述伤势轻重时按这个来"
        ]

    def labels(self) -> list[str]:
        return [] if self.major_wound_divisor == 2 else [f"重伤线 1/{self.major_wound_divisor}"]


_RAW = WoundConfig()


def is_major_wound(damage: int, max_hp: int, options: WoundConfig | None = None) -> bool:
    """单击伤害是否构成重伤：≥ 最大 HP ÷ major_wound_divisor（RAW 半血）。

    战斗结算（resolve_wound）与叙事扣血（turn_effects 的 HP_CHANGE）共用这一条口径。
    """
    opts = options or _RAW
    return damage >= max(1, max_hp) // opts.major_wound_divisor


def resolve_wound(
    hp: int, max_hp: int, damage: int, defender_data: dict, already_wounded: bool = False,
    options: WoundConfig | None = None,
) -> dict:
    """结算一次伤害，再让规则钩子 ``wound_resolved`` 追加效果（如流血）。

    返回 {new_hp, status, lines, conditions_add}：``conditions_add`` 是钩子要挂上的状态，
    由调用方（战斗服务）并入参战方的 conditions。
    """
    result = _resolve_wound_core(hp, max_hp, damage, defender_data, already_wounded, options)
    effects = merge_effects(hooks_of(options).wound_resolved(
        result=dict(result), damage=damage, max_hp=max(1, max_hp),
        defender_data=defender_data, options=options,
    ))
    result["lines"] = result["lines"] + effects["lines"]
    result["conditions_add"] = effects["conditions_add"]
    return result


def turn_start_damage(participant: dict, options: WoundConfig | None = None) -> list[dict]:
    """战斗中某参战方回合开始时的持续伤害（规则钩子 ``combat_turn_start``，如流血）。"""
    return [d for r in hooks_of(options).combat_turn_start(participant=participant, options=options)
            for d in (r or []) if d.get("damage")]


def after_first_aid(target: dict, options: WoundConfig | None = None) -> dict:
    """急救成功之后的效果（规则钩子 ``first_aid_applied``，如止血）。"""
    return merge_effects(hooks_of(options).first_aid_applied(target=target, options=options))


def _resolve_wound_core(
    hp: int, max_hp: int, damage: int, defender_data: dict, already_wounded: bool = False,
    options: WoundConfig | None = None,
) -> dict:
    """结算一次伤害的 HP 与状态迁移（纯规则，不碰 DB）。返回 {new_hp, status, lines}。

    CoC 7e：
    - 单次伤害 **> 最大 HP** → 当场死亡（dead）。
    - HP 归零：**受过重伤**（本击重伤 或 已带重伤标记 already_wounded）→ 濒死（dying）；
      **只受轻伤**归零 → 昏迷（unconscious，伤势稳定、不致死，会随时间恢复）。
    - 未归零的重伤（单击 ≥ 半血）→ 立刻过体质检定，失败则昏迷、成功则重伤（major_wound）。
    """
    opts = options or _RAW
    max_hp = max(1, max_hp)   # 归一非正 max_hp，避免半血阈值判定被负值扭曲
    new_hp = max(0, hp - damage)
    lines = [f"受到 {damage} 点伤害（HP {hp}→{new_hp}）"]
    major = is_major_wound(damage, max_hp, opts)
    if damage > max_hp:                                   # ① 单击超过满血 → 必死
        return {"new_hp": 0, "status": "dead", "lines": lines + ["单次伤害超过最大生命值，当场毙命！"]}
    if new_hp <= 0:                                       # ② 归零：分轻/重伤
        if major or already_wounded:
            return {"new_hp": 0, "status": "dying", "lines": lines + ["濒死，需急救稳住，否则每轮体质检定失败即死。"]}
        return {"new_hp": 0, "status": "unconscious",
                "lines": lines + ["昏迷倒地（生命值归零，但只受轻伤、伤势稳定、不致死）。"]}
    if major:                                             # ③ 未归零的重伤 → 体质检定判昏迷
        con = resolve_skill_check(defender_data, "体质", "normal", options=options)
        lines.append(f"重伤体质检定：{con.description}")
        if con.outcome in ("failure", "fumble"):
            return {"new_hp": new_hp, "status": "unconscious", "lines": lines + ["眼前一黑，昏迷倒地！"]}
        return {"new_hp": new_hp, "status": "major_wound", "lines": lines}
    return {"new_hp": new_hp, "status": "ok", "lines": lines}


def tick_dying(participant: dict, options: WoundConfig | None = None) -> list[str]:
    """濒死者每轮开始的 CON 检定：失败则死亡。非濒死者 no-op。原地改 participant。"""
    if participant.get("status") != "dying":
        return []
    data = {"skills": participant.get("skills") or {},
            "base_attributes": participant.get("base_attributes") or {},
            "system_data": participant.get("system_data") or {}}
    con = resolve_skill_check(data, "体质", "normal", options=options)
    if con.outcome in ("failure", "fumble"):
        participant["status"] = "dead"
        return [f"{participant.get('name', '')}｜濒死体质检定失败（{con.description}），气绝身亡。"]
    return [f"{participant.get('name', '')}｜濒死体质检定{con.description}，暂时挺住。"]


COMPONENT = Component(
    id="coc7.wound", title="伤害与状态",
    requires=("coc7.check",),
    config=WoundConfig, group="伤害与状态",
    provides={"wound": Provide(
        __name__,
        bound=("is_major_wound", "resolve_wound", "tick_dying", "turn_start_damage", "after_first_aid"),
        plain=("CHARACTER_STATUSES", "STATUS_SEVERITY", "VALID_STATUS_VALUES", "LEGACY_STATUS_ALIAS"),
    )},
)
