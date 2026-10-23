import asyncio

import pytest

from app.ai import call_budget, usage_tracker
from app.services import generation_manager
from app.config import settings


def test_子任务共享调用预算而不同回合隔离():
    async def run():
        async def child():
            call_budget.reserve()
        with call_budget.scope(max_calls=2, max_tokens=100, seconds=5) as budget:
            await asyncio.gather(child(), child())
            assert budget.calls == 2
            with pytest.raises(call_budget.BudgetExceeded):
                call_budget.reserve()
            assert not call_budget.allow_optional()
        with call_budget.scope(max_calls=2, max_tokens=100, seconds=5) as budget:
            call_budget.reserve()
            assert budget.calls == 1
    asyncio.run(run())


def test_服务端用量阻止后续请求但不丢弃当前结果():
    with call_budget.scope(max_calls=10, max_tokens=20, seconds=5):
        call_budget.reserve()
        usage_tracker.add({"prompt_tokens": 15, "completion_tokens": 10})
        with pytest.raises(call_budget.BudgetExceeded):
            call_budget.reserve()
    call_budget.reserve()  # 生成外的调用不受房间预算影响


def test_整轮超时会等待清理并释放生成锁(monkeypatch):
    monkeypatch.setattr(settings, "generation_max_seconds", 0.02)
    cleaned, chunks = [], []
    monkeypatch.setattr(generation_manager.room_hub, "broadcast", lambda _room, chunk: chunks.append(chunk))

    async def run():
        manager = generation_manager.GenerationManager()
        async def slow():
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.append(True)
        await manager.start("budget-room", slow())
        await asyncio.sleep(0)
        assert not manager.is_generating("budget-room")
    asyncio.run(run())
    assert cleaned == [True]
    assert chunks[-1].type == "done"
    assert any("预算" in (c.content or "") for c in chunks)
