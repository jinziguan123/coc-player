"""示例规则插件：随机遭遇表——给 KP 加一个「掷一次随机遭遇」的工具。

演示组件怎样给 KP 新增工具（``kp_tools``）：打开组件后，KP 在调查员长途跋涉、夜里守夜、
在野外或荒僻处逗留时可以调用 ``roll_encounter``，由系统掷 1D6 查表，结果回给 KP 去演。
掷骰交给系统而不是让 KP 自己「想一个」，遭遇才真的是随机的，也不会总挑最有戏剧性的那个。

安装：把本文件复制到插件目录（源码运行是 server/plugins/，打包版是数据目录下的 plugins/），
重启应用，在侧栏「规则」页的「探索」分区里打开「随机遭遇」。插件写法见同目录 README.md。
"""

import random

from app.rules.config import RuleConfig, rule_field
from app.rules.kernel import Component
from app.rules.kp_tools import KpTool, KpToolContext

PLUGIN_API_VERSION = 1
RULE_SYSTEM = "coc"
NAME = "随机遭遇"
VERSION = "1.0"
DESCRIPTION = "给 KP 一个随机遭遇表工具：赶路、守夜、野外逗留时由系统掷骰决定遇到什么"

#: 1D6 遭遇表。想换内容直接改这里。
ENCOUNTERS = {
    "wild": [
        "什么也没发生，只有风声",
        "远处传来不知名动物的叫声，随即戛然而止",
        "路边有一串新鲜的脚印，通向树林深处",
        "一位迷路的旅人，神色慌张，说话前言不搭后语",
        "一具动物尸体，伤口整齐得不像野兽所为",
        "短暂地瞥见树影间有个高大的身影，再看时已经不见了",
    ],
    "town": [
        "什么也没发生，街上冷清",
        "一个报童塞来一份当天的报纸，头版有一则奇怪的失踪启事",
        "有人在街角盯着调查员，被发现后转身混进了人群",
        "一家店铺的橱窗里摆着一件和案子有关的旧物",
        "一位老人主动搭话，似乎知道些什么，却只肯说一半",
        "警察在盘查路人，问的正是调查员在找的那个人",
    ],
}


class RandomEncounterConfig(RuleConfig):
    random_encounter: bool = rule_field(False, title="随机遭遇")

    def describe(self) -> list[str]:
        if not self.random_encounter:
            return []
        return ["随机遭遇（本桌插件）：赶路、守夜或在荒僻处逗留时，可调用 roll_encounter 掷随机遭遇"]

    def labels(self) -> list[str]:
        return ["随机遭遇·插件"] if self.random_encounter else []


def roll_encounter(ctx: KpToolContext, args: dict) -> str:
    where = args.get("where") if args.get("where") in ENCOUNTERS else "wild"
    roll = random.randint(1, 6)
    result = ENCOUNTERS[where][roll - 1]
    return (
        f"随机遭遇（1D6={roll}）：{result}。"
        "把它自然地写进当前场景；它是一段插曲，不必和主线强行挂钩，也不要向玩家提到掷了遭遇表。"
    )


COMPONENTS = [
    Component(
        id="example.random-encounter", title="随机遭遇",
        config=RandomEncounterConfig, toggle="random_encounter", group="探索",
        description="插件提供：KP 可以掷随机遭遇表，让赶路与守夜不只是一句「一夜无事」。",
        kp_tools=(
            KpTool(
                name="roll_encounter",
                description=(
                    "掷一次随机遭遇（系统掷 1D6 查表）。只在调查员长途赶路、夜里守夜、"
                    "在野外或荒僻处逗留一段时间时用，一个场景至多一次；正在调查、对话或战斗时不要用。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "where": {
                            "type": "string",
                            "enum": ["wild", "town"],
                            "description": "所在环境：wild 野外/荒僻处，town 城镇街道",
                        },
                    },
                    "required": ["where"],
                },
                execute=roll_encounter,
            ),
        ),
    ),
]
