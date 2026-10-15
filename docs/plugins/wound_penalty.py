"""示例规则插件：受伤减值（常见村规）——生命值低于一定比例时，所有检定多一颗惩罚骰。

安装：把本文件复制到插件目录（源码运行是 server/plugins/，打包版是数据目录下的 plugins/），
重启应用，在侧栏「规则」页的「检定」分区里打开「受伤减值」。插件写法见同目录 README.md。
"""

from app.rules.config import RuleConfig, rule_field
from app.rules.hooks import hookimpl
from app.rules.kernel import Component

PLUGIN_API_VERSION = 1
RULE_SYSTEM = "coc"
NAME = "受伤减值"
VERSION = "1.0"
DESCRIPTION = "生命值低于一定比例时，所有检定多一颗惩罚骰（常见村规）"


class WoundPenaltyConfig(RuleConfig):
    wound_penalty: bool = rule_field(False, title="受伤减值")
    wound_penalty_percent: int = rule_field(
        50, ge=10, le=90, title="生命值低于（%）",
        hint="当前 HP 低于最大 HP 的 {value}% 时，所有检定多一颗惩罚骰",
    )

    def describe(self) -> list[str]:
        if not self.wound_penalty:
            return []
        return [
            f"受伤减值（本桌插件）：生命值低于最大值的 {self.wound_penalty_percent}% 时，"
            "所有检定多一颗惩罚骰。描写重伤者行动时可以带出伤势的拖累",
        ]

    def labels(self) -> list[str]:
        return [f"受伤减值 <{self.wound_penalty_percent}%·插件"] if self.wound_penalty else []


@hookimpl
def check_modifiers(character_data: dict, options) -> list[dict] | None:
    hp = (character_data.get("system_data") or {}).get("hitPoints") or {}
    current, maximum = hp.get("current"), hp.get("max")
    if not isinstance(current, int) or not isinstance(maximum, int) or maximum <= 0:
        return None
    percent = getattr(options, "wound_penalty_percent", 50)
    if current * 100 >= maximum * percent:
        return None
    return [{"kind": "penalty", "n": 1, "reason": "伤势拖累了动作"}]


COMPONENTS = [
    Component(
        id="example.wound-penalty", title="受伤减值",
        config=WoundPenaltyConfig, toggle="wound_penalty", group="检定",
        description="插件提供的常见村规：生命值低于一定比例时，所有检定多一颗惩罚骰。",
        hooks=__name__,
    ),
]
