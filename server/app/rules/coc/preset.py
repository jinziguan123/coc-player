"""CoC 7e 官方预设：一张组件清单。

顺序即 KP 描述、玩家摘要标签与村规面板的顺序（判定在前，伤害与状态在后）。
每个组件的参数、算法与声明都在 ``components/<名字>.py`` 里。
"""

from __future__ import annotations

from app.rules.coc.components import (
    character,
    chase,
    check,
    combat,
    dice,
    equipment,
    growth,
    luck,
    luck_reroll,
    luck_spend,
    madness,
    occupation,
    positioning,
    sanity,
    wound,
)
from app.rules.coc.options import CocRuleOptions
from app.rules.kernel import RuleSystem

OFFICIAL_COMPONENTS = (
    dice, character, occupation, equipment,
    check, luck, luck_spend, luck_reroll,
    wound, combat, positioning, chase,
    sanity, madness, growth,
)


def build_system() -> RuleSystem:
    components = tuple(m.COMPONENT for m in OFFICIAL_COMPONENTS)
    return RuleSystem(
        id="coc",
        title="克苏鲁的呼唤 第七版",
        version=1,
        components=components,
        preset=tuple(c.id for c in components),
        options_model=CocRuleOptions,
    )
