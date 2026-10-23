"""一次生成共享的调用预算；子任务继承同一本账，不影响生成外的素材解析。"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import time


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class CallBudget:
    max_calls: int
    max_tokens: int
    seconds: float
    calls: int = 0
    tokens: int = 0
    started: float = field(default_factory=time.monotonic)

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.seconds - (time.monotonic() - self.started))

    def reserve(self) -> None:
        if self.calls >= self.max_calls or self.tokens >= self.max_tokens or not self.remaining_seconds:
            raise BudgetExceeded("本回合已达到 AI 调用预算，已停止继续请求；已结算的结果保留。")
        self.calls += 1


_current: ContextVar[CallBudget | None] = ContextVar("generation_call_budget", default=None)


@contextmanager
def scope(*, max_calls: int, max_tokens: int, seconds: float):
    budget = CallBudget(max_calls, max_tokens, seconds)
    token = _current.set(budget)
    try:
        yield budget
    finally:
        _current.reset(token)


def reserve() -> None:
    budget = _current.get()
    if budget is not None:
        budget.reserve()


def record(usage: dict) -> None:
    budget = _current.get()
    if budget is not None:
        budget.tokens += int(usage.get("total_tokens") or (
            int(usage.get("prompt_tokens") or 0) + int(usage.get("completion_tokens") or 0)
        ))


def allow_optional() -> bool:
    budget = _current.get()
    return budget is None or (
        budget.remaining_seconds > 15 and budget.calls + 2 < budget.max_calls
        and budget.tokens < budget.max_tokens * 0.9
    )


async def detached(awaitable):
    """在不属于本回合的后台任务里运行：create_task 会复制上下文，否则配图等
    旁路调用会消耗、甚至被已用尽的回合预算拦下。"""
    token = _current.set(None)
    try:
        return await awaitable
    finally:
        _current.reset(token)
