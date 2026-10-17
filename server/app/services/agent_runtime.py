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

    def fit(self) -> "ContextBundle":
        """按 budget_tokens 裁掉最旧的历史消息；system 与最后一条消息始终保留。

        这是跨 Agent 的兜底预算，不替代 KP 自己的分段预算。估算口径与上下文模块一致
        （约 3 字符 = 1 token）；只做减法，不重写内容。
        """
        def _cost(text: str) -> int:
            return max(1, len(str(text or "")) // 3)

        messages = [dict(m) for m in self.messages]
        total = _cost(self.system) + sum(_cost(m.get("content")) for m in messages)
        while len(messages) > 1 and total > self.budget_tokens:
            dropped = messages.pop(0)
            total -= _cost(dropped.get("content"))
        return ContextBundle(
            system=self.system,
            messages=messages,
            budget_tokens=self.budget_tokens,
            visibility=self.visibility,
            metadata=self.metadata,
        )

    def for_model(self) -> list[dict]:
        if not self.system:
            return [dict(m) for m in self.messages]
        return [{"role": "system", "content": self.system}] + [dict(m) for m in self.messages]
