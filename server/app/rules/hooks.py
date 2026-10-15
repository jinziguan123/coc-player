"""规则钩子：组件在规则流程的固定节点上「挂」自己的逻辑（pluggy）。

服务槽位（``rs.<槽位>``）解决「谁来算」，独占；钩子解决「算的前后还要发生什么」，可叠加——
疯狂发作给检定加惩罚骰、流血在重伤后挂上持续失血、Pulp 换掉最大 HP 的算法，都不必改
检定或伤害组件本身。

每份生效规则（Ruleset）有自己的 ``PluginManager``，只登记**本局实际启用**的组件的钩子：
可选组件的开关关着，它的钩子就不存在。钩子调用由组件算法自己发起（如
``check.resolve_skill_check`` 调 ``check_modifiers``），拿到的 ``hooks`` 随注入的
``options`` 一起来（见 :func:`hooks_of`）；没有经 Ruleset 注入时（单测直接调）一个钩子都没有。

写钩子的组件：在组件文件里用 ``@hookimpl`` 标注函数（函数名 = 钩子名，参数取钩子声明里的
任意子集），并在 ``Component(hooks=__name__)`` 里声明。名字拼错会在启动时报错，不会静默不生效。
"""

from __future__ import annotations

from typing import Any

import pluggy

PROJECT = "coc_player_rules"

hookspec = pluggy.HookspecMarker(PROJECT)
hookimpl = pluggy.HookimplMarker(PROJECT)


class RuleHookSpecs:
    """所有钩子的声明。新增钩子 = 在这里加一个方法，并在调用点调用它。"""

    @hookspec
    def check_modifiers(self, character_data: dict, skill_name: str, difficulty: str, options: Any) -> list[dict] | None:
        """检定掷骰之前：返回要追加的奖惩骰。

        每项 ``{"kind": "bonus" | "penalty", "n": int, "reason": str}``；理由会显示在骰子卡上。
        追加后仍受村规「奖惩骰上限」约束。
        """

    @hookspec(firstresult=True)
    def max_hp(self, attrs: dict, options: Any) -> int | None:
        """最大 HP 的算法：第一个给出非 None 的实现生效（官方实现以 ``trylast`` 兜底）。"""

    @hookspec
    def wound_resolved(
        self, result: dict, damage: int, max_hp: int, defender_data: dict, options: Any,
    ) -> dict | None:
        """一次伤害结算之后：返回 ``{"conditions_add": [...], "lines": [...]}``。

        ``result`` 是伤害结算的结果（new_hp / status / lines），只读；要追加的状态与播报写在返回值里。
        """

    @hookspec
    def combat_turn_start(self, participant: dict, options: Any) -> list[dict] | None:
        """战斗中某参战方的回合开始时：返回持续伤害 ``[{"damage": int, "reason": str}]``。"""

    @hookspec
    def first_aid_applied(self, target: dict, options: Any) -> dict | None:
        """急救成功之后：返回 ``{"conditions_remove": [...], "lines": [...]}``。"""

    @hookspec
    def condition_notes(self, conditions: list[str], options: Any) -> list[str] | None:
        """角色卡上挂着持续状态（``system_data.conditions``）时，告诉 KP 怎么演、何时结算。

        战斗外没有「回合」，持续状态不自动结算；这几句话进 KP 上下文，由它按叙事时间裁定。
        """


def new_manager() -> pluggy.PluginManager:
    pm = pluggy.PluginManager(PROJECT)
    pm.add_hookspecs(RuleHookSpecs)
    return pm


#: 没有任何组件钩子的空管理器：未经 Ruleset 注入时用它，调用照常返回 [] / None。
_EMPTY = new_manager()


def hooks_of(options: Any) -> Any:
    """取本局的钩子（``pm.hook``）。``options`` 为 None 或未经 Ruleset 注入时一个钩子都没有。"""
    hooks = getattr(options, "_hooks", None) if options is not None else None
    return hooks if hooks is not None else _EMPTY.hook


def merge_effects(results: list[dict | None]) -> dict:
    """把多个钩子实现返回的 ``{conditions_add, conditions_remove, lines}`` 合成一份。"""
    out: dict[str, list] = {"conditions_add": [], "conditions_remove": [], "lines": []}
    for r in results:
        for key in out:
            out[key] += list((r or {}).get(key) or [])
    return out
