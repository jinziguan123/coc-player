"""通用 Agent 上下文抽象。

不是调度器，只是把各 Agent 共用的「系统提示 + 消息 + 预算 + 可见范围」收成一个值对象。
NPC 对话、队友决策先接进来；planner/combat/backstage 可以按同一接口逐步迁移，避免每个
Agent 再各写一套 build_*_context 的返回约定。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import json

from app.ai.token_estimate import estimate_text_tokens


class ContextBudgetExceeded(ValueError):
    """必须保留的系统约束或当前输入已超过预算，拒绝静默截断。"""



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
        """按 KP 同口径裁历史；保留系统消息、最新输入和完整工具交互组。

        超预算时不截断人设/安全约束/当前输入，而是显式失败，让调用方按职责降级。
        visibility 仅描述已构建的上下文；数据过滤必须在构建阶段完成。
        """
        if self.budget_tokens <= 0:
            raise ValueError("上下文预算必须为正整数")

        def cost(message: dict) -> int:
            content = message.get("content") or ""
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False)
            tools = json.dumps(message.get("tool_calls") or [], ensure_ascii=False)
            return 4 + estimate_text_tokens(content) + estimate_text_tokens(tools)

        messages = [dict(m) for m in self.messages]
        groups: list[list[dict]] = []
        for message in messages:
            if message.get("role") == "tool" and groups:
                groups[-1].append(message)
            else:
                groups.append([message])
        total = estimate_text_tokens(self.system) + sum(cost(m) for m in messages)
        dropped = 0
        # system 消息无论在何处都保留，工具调用及其返回作为一个整体删除。
        kept = []
        # 最新输入＝最后一条 user 消息；工具续写轮的末组是工具交互，玩家输入在其之前，同样钉住。
        last_user = max(
            (i for i, g in enumerate(groups) if g[0].get("role") == "user"), default=len(groups) - 1,
        )
        for index, group in enumerate(groups):
            pinned = (
                index in (len(groups) - 1, last_user)
                or any(m.get("role") == "system" for m in group)
            )
            if total > self.budget_tokens and not pinned:
                total -= sum(cost(m) for m in group)
                dropped += len(group)
            else:
                kept.extend(group)
        if total > self.budget_tokens:
            raise ContextBudgetExceeded(
                f"必须保留的上下文约 {total} token，超过预算 {self.budget_tokens}；未截断约束或当前输入。"
            )
        return ContextBundle(
            system=self.system, messages=kept, budget_tokens=self.budget_tokens,
            visibility=self.visibility,
            metadata={**self.metadata, "estimated_tokens": total, "dropped_messages": dropped},
        )

    def for_model(self) -> list[dict]:
        if not self.system:
            return [dict(m) for m in self.messages]
        return [{"role": "system", "content": self.system}] + [dict(m) for m in self.messages]
