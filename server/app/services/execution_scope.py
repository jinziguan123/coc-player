"""Agent 执行作用域与回合协调意图。

分头行动不是「同一段代码多跑几次」：每个组有自己的场景、成员和可执行工具边界。
这里把「这一组是谁、在哪、能改什么」显式化；会改全局状态的工具不在组内直接执行，
而是产出 CoordinatorIntent，交给回合协调器统一裁定。
"""
from __future__ import annotations

from dataclasses import dataclass, field


#: 只影响本组/本组成员的工具，可在组内 loop 直接执行。
LOCAL_TOOLS = frozenset({
    "dice_check", "opposed_check", "san_check", "npc_act", "say",
    "hp_change", "travel_suggest", "recall_history", "rule_lookup",
    "module_lookup", "set_mood", "mark_seen",
})

#: 会改整个会话状态、必须由回合协调器统一执行的工具。
COORDINATED_TOOLS = frozenset({
    "scene_change", "start_combat", "start_chase",
})


@dataclass(frozen=True)
class ExecutionScope:
    group_id: str
    scene_id: str
    actor_ids: tuple[str, ...]
    is_primary_group: bool = False
    split_mode: bool = True

    def contains(self, actor_id: str | None) -> bool:
        return bool(actor_id) and actor_id in self.actor_ids


@dataclass(frozen=True)
class CoordinatorIntent:
    kind: str
    scope: ExecutionScope
    args: dict = field(default_factory=dict)
