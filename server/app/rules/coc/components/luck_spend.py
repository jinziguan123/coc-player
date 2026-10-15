"""coc7.luck-spend：幸运消费（CoC 7e 官方可选规则，Keeper Rulebook p.99；默认关）。

可选组件：开关即参数 ``luck_spend``。只带参数与文案，结算在 :mod:`luck`。
"""

from __future__ import annotations

from app.rules.config import RuleConfig, rule_field
from app.rules.kernel import Component


class LuckSpendConfig(RuleConfig):
    """coc7.luck-spend：花幸运抵差值（CoC 7e 官方可选规则，Keeper Rulebook p.99；默认关）。"""

    luck_spend: bool = rule_field(False, title="幸运消费")
    luck_spend_max: int = rule_field(
        0, ge=0, le=999, title="单次上限", hint="一次检定最多花几点，0 = 不限",
    )
    luck_spend_in_combat: bool = rule_field(
        True, title="战斗中可用", hint="常见做法是战斗中禁用，避免拿幸运硬扛伤害判定",
    )
    luck_spend_blocks_improvement: bool = rule_field(
        True, title="买来的成功不计成长", hint="照规则书：走运没教会你任何事",
    )

    def manual_text(self) -> str:
        """KP 手册里的幸运消费说明（与 luck.rescue_offer 的边界逐条对应）。"""
        state = "本桌已开启" if self.luck_spend else (
            "默认不开启，【本桌改过的规则】写明已开启时才有"
        )
        return (
            f"幸运消耗（规则书的可选规则，{state}）：检定失败后玩家可花幸运点 1 点抵 1，"
            "把骰值降到本次要求难度的及格线；大失败、理智检定、幸运检定、孤注一掷后的骰都不能用；"
            "幸运值不恢复。系统会在失败后主动问玩家买不买，你不必替他们提议"
        )

    def describe(self) -> list[str]:
        if not self.luck_spend:
            return []
        detail = "玩家检定失败后可花幸运点抵掉差值买下成功"
        if self.luck_spend_max:
            detail += f"，单次最多 {self.luck_spend_max} 点"
        if not self.luck_spend_in_combat:
            detail += "，战斗中不可用"
        return [
            f"幸运消费：已开启——{detail}。"
            "所以别把一次失败写成『已成定局、再无转圜』，玩家可能正要花幸运翻盘"
        ]

    def labels(self) -> list[str]:
        if not self.luck_spend:
            return []
        extra: list[str] = []
        if self.luck_spend_max:
            extra.append(f"单次 ≤ {self.luck_spend_max} 点")
        if not self.luck_spend_in_combat:
            extra.append("战斗中不可用")
        if not self.luck_spend_blocks_improvement:
            extra.append("仍计成长")
        suffix = f"（{'、'.join(extra)}）" if extra else ""
        return [f"幸运消费{suffix}"]


COMPONENT = Component(
    id="coc7.luck-spend", title="幸运消费",
    requires=("coc7.luck",),
    config=LuckSpendConfig, toggle="luck_spend", group="幸运", tag="官方可选",
    description="规则书的可选规则：检定失败后可花幸运点抵掉差值买成功。开启后失败时会问玩家买不买。",
)
