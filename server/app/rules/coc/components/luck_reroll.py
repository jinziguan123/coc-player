"""coc7.luck-reroll：燃运重骰（**不是官方规则**，常见村规；默认关）。

可选组件：开关即参数 ``luck_reroll``。只带参数与文案，结算在 :mod:`luck`。
"""

from __future__ import annotations

from app.rules.config import RuleConfig, rule_field
from app.rules.kernel import Component


class LuckRerollConfig(RuleConfig):
    """coc7.luck-reroll：燃运重骰（**不是官方规则**，常见村规；默认关）。

    第七版规则书 p.85 只给了「1:1 花幸运改变掷骰结果」这一种用法，没有花幸运重掷的条目；
    重掷在原文里叫**孤注一掷**，它不花幸运，代价是失败后果加重，而且与花幸运二选一。
    """

    luck_reroll: bool = rule_field(False, title="燃运重骰")
    luck_reroll_cost: int = rule_field(
        10, ge=1, le=99, title="每次烧几点",
        hint="买的是一次机会不是一次成功，所以是定价，不随差多少变",
    )

    def describe(self) -> list[str]:
        if not self.luck_reroll:
            return []
        return [
            f"燃运重骰（本桌自定，非规则书内容）：失败后可烧 {self.luck_reroll_cost} 点幸运"
            "整骰重掷一次，重掷结果照单全收。同样别把一次失败写死"
        ]

    def labels(self) -> list[str]:
        # 非官方规则，标出来——玩家有权知道哪条不是规则书里的
        return [f"燃运重骰 {self.luck_reroll_cost} 点·本桌自定"] if self.luck_reroll else []


COMPONENT = Component(
    id="coc7.luck-reroll", title="燃运重骰",
    requires=("coc7.luck",),
    config=LuckRerollConfig, toggle="luck_reroll", group="判定",
    description="本桌自定，规则书里没有这条：失败后可烧固定点数幸运，整骰重掷一次。"
                "规则书的重掷叫「孤注一掷」，它不花幸运、且与花幸运二选一。",
)
