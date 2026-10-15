"""coc7.growth：技能成长检定（战后结算）。可选组件：开关即参数 ``improvement``。

槽位 ``rs.growth``。
"""

from __future__ import annotations

from app.rules.config import RuleConfig, rule_field
from app.rules.dice import roll, roll_percentile
from app.rules.kernel import Component, Provide


class GrowthConfig(RuleConfig):
    """coc7.growth：技能成长（默认开；一夜之间的短模组常关掉）。"""

    improvement: bool = rule_field(True, title="技能成长")

    def describe(self) -> list[str]:
        if self.improvement:
            return []
        return ["本局不做技能成长检定，收场时不要提『这次经历让你的技能有所长进』"]

    def labels(self) -> list[str]:
        return [] if self.improvement else ["不做成长检定"]


_RAW = GrowthConfig()


def improvement_check(current_value: int, options: GrowthConfig | None = None) -> dict | None:
    """CoC 成长检定：d100 > 当前技能值（或 > 95）即成长，+1d10（上限 99）。

    返回 ``{"roll", "improved", "gain", "old_value", "new_value"}``；村规关掉成长时返回 None。
    """
    if not (options or _RAW).improvement:
        return None
    r = roll_percentile()
    improved = r > current_value or r > 95
    gain = roll("1d10").total if improved else 0
    new_value = min(current_value + gain, 99) if improved else current_value
    return {
        "roll": r,
        "improved": improved and new_value > current_value,
        "gain": new_value - current_value,
        "old_value": current_value,
        "new_value": new_value,
    }


COMPONENT = Component(
    id="coc7.growth", title="技能成长",
    requires=("coc7.check",),
    config=GrowthConfig, toggle="improvement", group="伤害与状态",
    description="关掉则模组结束时不做成长检定（短模组常这么跑）",
    provides={"growth": Provide(__name__, bound=("improvement_check",))},
)
