"""coc7.bleeding：流血（常见村规，规则书没有；默认关）。

可选组件：开关即参数 ``bleeding``。它不提供服务，只实现四个钩子：

- ``wound_resolved``：一击构成重伤且人还活着 → 挂上「流血」状态（战斗与叙事扣血都走这里）；
- ``combat_turn_start``：流血者每轮回合开始失血若干点；
- ``first_aid_applied``：急救成功 → 止血（战斗内的急救动作与叙事中的急救/医学检定都走这里）；
- ``condition_notes``：战斗外没有「回合」，**不自动扣血**——告诉 KP 谁在流血，由它按叙事里
  过去的时间用 [HP_CHANGE] 结算。

状态存在角色卡 ``system_data.conditions``，进战斗时带进参战方、战斗中的变化写回角色卡。
"""

from __future__ import annotations

from app.rules.coc.components.wound import is_major_wound
from app.rules.config import RuleConfig, rule_field
from app.rules.hooks import hookimpl
from app.rules.kernel import Component

BLEEDING = "bleeding"


class BleedingConfig(RuleConfig):
    bleeding: bool = rule_field(False, title="流血")
    bleed_damage: int = rule_field(
        1, ge=1, le=5, title="每轮失血", hint="流血者每轮回合开始失去的 HP",
    )

    def describe(self) -> list[str]:
        if not self.bleeding:
            return []
        return [
            f"流血（本桌自定，非规则书内容）：受重伤会流血，战斗中每轮回合开始失血 {self.bleed_damage} 点，"
            "急救成功即止血。描写重伤时可以带出血流不止的紧迫感",
        ]

    def labels(self) -> list[str]:
        return [f"流血 每轮 {self.bleed_damage} 点·本桌自定"] if self.bleeding else []


_RAW = BleedingConfig()


@hookimpl
def wound_resolved(result: dict, damage: int, max_hp: int, options) -> dict | None:
    if result.get("status") == "dead" or not is_major_wound(damage, max_hp, options):
        return None
    n = (options or _RAW).bleed_damage
    return {"conditions_add": [BLEEDING], "lines": [f"伤口血流不止（每轮失血 {n} 点，急救可止血）。"]}


@hookimpl
def combat_turn_start(participant: dict, options) -> list[dict] | None:
    if BLEEDING not in (participant.get("conditions") or []):
        return None
    return [{"damage": (options or _RAW).bleed_damage, "reason": "失血"}]


@hookimpl
def first_aid_applied(target: dict) -> dict | None:
    if BLEEDING not in (target.get("conditions") or []):
        return None
    return {"conditions_remove": [BLEEDING], "lines": ["血止住了。"]}


@hookimpl
def condition_notes(conditions: list[str], options) -> list[str] | None:
    if BLEEDING not in conditions:
        return None
    n = (options or _RAW).bleed_damage
    return [
        f"流血中：战斗里每轮失血 {n} 点（系统结算）；战斗外系统不自动扣血——叙事里明显过了一段时间"
        f"（拖着伤赶路、耽搁许久）而仍未止血时，由你用 [HP_CHANGE] 扣 {n} 点并写明「失血」。"
        "急救或医学检定成功即止血",
    ]


COMPONENT = Component(
    id="coc7.bleeding", title="流血",
    requires=("coc7.wound",),
    config=BleedingConfig, toggle="bleeding", group="伤害与状态", tag="本桌自定",
    description="本桌自定，规则书里没有这条：受重伤会流血，战斗中每轮回合开始失血，急救成功即止血；"
    "战斗外不自动失血，由 KP 按叙事时间裁定。",
    conditions={BLEEDING: "流血"},
    hooks=__name__,
)
