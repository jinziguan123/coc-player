"""通用 Agent 上下文抽象。

不是调度器，只是把各 Agent 共用的「系统提示 + 消息 + 预算 + 可见范围」收成一个值对象。
NPC 对话、队友决策先接进来；planner/combat/backstage 可以按同一接口逐步迁移，避免每个
Agent 再各写一套 build_*_context 的返回约定。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ContextBundle:
    system: str
    messages: list[dict]
    budget_tokens: int
    visibility: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_messages(
        cls, messages: list[dict], *,
        budget_tokens: int,
        visibility: tuple[str, ...] = (),
        metadata: dict[str, Any] | None = None,
    ) -> "ContextBundle":
        msgs = [dict(m) for m in (messages or [])]
        system = ""
        if msgs and msgs[0].get("role") == "system":
            system = str(msgs[0].get("content") or "")
            msgs = msgs[1:]
        return cls(
            system=system,
            messages=msgs,
            budget_tokens=budget_tokens,
            visibility=visibility,
            metadata=dict(metadata or {}),
        )

    def for_model(self) -> list[dict]:
        if not self.system:
            return [dict(m) for m in self.messages]
        return [{"role": "system", "content": self.system}] + [dict(m) for m in self.messages]
