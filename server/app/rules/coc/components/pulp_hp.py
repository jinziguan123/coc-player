"""coc7.pulp-hp：Pulp 英雄生命值（《Pulp 克苏鲁》的英雄规则；默认关）。

可选组件：开关即参数 ``pulp_hp``。它不提供服务，只实现钩子 ``max_hp``——把官方的
(CON+SIZ)/10 换成 (CON+SIZ)/5。官方实现以 ``trylast`` 挂着，这里的实现排在它前面先生效；
关掉开关，本组件连同钩子一起不启用，自然回到官方算法。

只影响**之后算出来的** HP：新建的角色、新加入战斗且没写 hp 的 NPC。已有角色卡上存着的 HP
不会被改写——中途开关它不会让谁的血量突然翻倍或减半。
"""

from __future__ import annotations

from app.rules.config import RuleConfig, rule_field
from app.rules.hooks import hookimpl
from app.rules.kernel import Component


class PulpHpConfig(RuleConfig):
    pulp_hp: bool = rule_field(False, title="Pulp 英雄生命值")

    def describe(self) -> list[str]:
        if not self.pulp_hp:
            return []
        return [
            "Pulp 英雄生命值：新建角色与 NPC 的最大 HP 按 (CON+SIZ)/5 计算，约为常规的两倍——"
            "调查员是能扛事的英雄，描写伤势时别按常人的脆弱程度来",
        ]

    def labels(self) -> list[str]:
        return ["Pulp 英雄生命值"] if self.pulp_hp else []


@hookimpl
def max_hp(attrs: dict) -> int:
    return (attrs.get("CON", 50) + attrs.get("SIZ", 50)) // 5


COMPONENT = Component(
    id="coc7.pulp-hp", title="Pulp 英雄生命值",
    requires=("coc7.character",),
    config=PulpHpConfig, toggle="pulp_hp", group="伤害与状态",
    description="《Pulp 克苏鲁》的英雄规则：最大 HP 按 (CON+SIZ)/5 计算，约为常规的两倍。"
                "只影响之后新建的角色与 NPC，已有角色卡上的 HP 不变。",
    hooks=__name__,
)
