"""生成/收尾任务生命周期的故障契约：取消、超时、预算、异常读取与任务身份。"""
from __future__ import annotations

import asyncio
import gc
import inspect
import warnings

import pytest

from app.ai import call_budget
from app.config import settings
from app.services import generation_lifecycle
from app.services.generation_manager import GenerationManager
from app.services.room_hub import room_hub


@pytest.fixture
def hub(monkeypatch):
    sent: list = []
    ended: list = []
    monkeypatch.setattr(room_hub, "broadcast", lambda room, chunk, **k: sent.append(chunk.type))
    monkeypatch.setattr(room_hub, "begin_generation", lambda room: None)
    monkeypatch.setattr(room_hub, "end_generation", lambda room: ended.append(room))
    return sent, ended


def test_吞取消的入口超时后仍释放锁并补发_done(hub, monkeypatch):
    sent, ended = hub
    monkeypatch.setattr(settings, "generation_max_seconds", 0.05)
    exits = []

    async def entry():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            exits.append("swallowed")   # 与 run_chat_generation 相同：吞掉取消、正常返回

    async def main():
        manager = GenerationManager()
        task = manager.start("r", entry())
        await task
        await asyncio.sleep(0)
        assert not manager.is_generating("r")
        assert "r" not in manager._tasks

    asyncio.run(main())
    assert exits == ["swallowed"]
    assert sent[-2:] == ["system", "done"]
    assert ended == ["r"]


def test_吞取消的入口被手动取消时等待结束并释放锁(hub):
    sent, ended = hub
    started = asyncio.Event

    async def main():
        ready = started()

        async def entry():
            ready.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                await asyncio.sleep(0)   # 取消后仍有收尾 await

        manager = GenerationManager()
        manager.start("r", entry())
        await ready.wait()
        await manager.cancel("r")
        assert not manager.is_generating("r")
        assert "r" not in manager._tasks
        # 释放后可立即开新一轮
        second = manager.start("r", asyncio.sleep(0))
        await second

    asyncio.run(main())
    assert ended == ["r", "r"]
    assert "done" not in sent   # 取消不是预算超限，不由管理器代发 done


def test_首次调度前取消会关闭原协程(hub):
    holder = {}

    async def entry():
        await asyncio.sleep(0)

    async def main():
        manager = GenerationManager()
        coro = entry()
        holder["coro"] = coro
        task = manager.start("r", coro)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)
        assert not manager.is_generating("r")

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        asyncio.run(main())
        gc.collect()
    assert inspect.getcoroutinestate(holder["coro"]) == inspect.CORO_CLOSED


def test_旧任务完成回调不清除新任务登记(hub):
    async def main():
        manager = GenerationManager()
        old = asyncio.create_task(asyncio.sleep(0))
        new = asyncio.create_task(asyncio.sleep(0.01))
        await old
        manager._tasks["r"] = new
        manager._on_done("r", old)
        assert manager._tasks["r"] is new
        await new

    asyncio.run(main())


def test_未捕获的预算超限由管理器收口(hub, monkeypatch):
    sent, ended = hub
    monkeypatch.setattr(settings, "generation_max_calls", 1)

    async def entry():
        call_budget.reserve()
        call_budget.reserve()   # 第二次越界

    async def main():
        manager = GenerationManager()
        await manager.start("r", entry())
        await asyncio.sleep(0)

    asyncio.run(main())
    assert sent[-2:] == ["system", "done"] and ended == ["r"]


def test_子任务继承回合预算而脱离任务不继承():
    seen = {}

    async def main():
        with call_budget.scope(max_calls=2, max_tokens=10, seconds=10) as budget:
            async def child():
                call_budget.reserve()
                seen["child"] = call_budget._current.get()

            async def background():
                seen["background"] = call_budget._current.get()
                call_budget.reserve()   # 无预算时不应抛

            await asyncio.create_task(child())
            await asyncio.create_task(call_budget.detached(background()))
            assert budget.calls == 1
            seen["outer"] = call_budget._current.get()

    asyncio.run(main())
    assert seen["child"] is seen["outer"]
    assert seen["background"] is None


# ── 后台收尾 ──────────────────────────────────────────────────────────────


def test_未排空时再次启动收尾会串行执行且不丢旧任务():
    order: list[str] = []
    gate = asyncio.Event

    async def main():
        release = gate()
        manager = generation_lifecycle.HousekeepingManager()

        async def first(db, sid, llm):
            order.append("first:start")
            await release.wait()
            order.append("first:end")

        async def second(db, sid, llm):
            order.append("second")

        async def noop(db, sid, llm):
            pass

        manager.spawn("s", None, first, noop)
        await asyncio.sleep(0.01)
        manager.spawn("s", None, second, noop)
        await asyncio.sleep(0.01)
        assert order == ["first:start"]   # 第二个在等第一个
        release.set()
        await manager.drain("s")
        assert "s" not in manager.tasks

    asyncio.run(main())
    assert order == ["first:start", "first:end", "second"]


def test_无人排空的收尾异常也被及时读取(caplog):
    async def main():
        manager = generation_lifecycle.HousekeepingManager()

        async def boom(db, sid, llm):
            raise RuntimeError("收尾探针")

        async def noop(db, sid, llm):
            pass

        manager.spawn("s", None, boom, noop)
        await asyncio.sleep(0.05)
        assert "s" not in manager.tasks   # 完成即注销
        await manager.drain("s")          # 不应再抛或重复记录

    asyncio.run(main())
    assert caplog.text.count("后台收尾失败") == 1


def test_收尾使用独立预算而非继承回合预算(monkeypatch):
    seen = {}

    async def main():
        manager = generation_lifecycle.HousekeepingManager()

        async def probe(db, sid, llm):
            seen["budget"] = call_budget._current.get()

        async def noop(db, sid, llm):
            pass

        with call_budget.scope(max_calls=1, max_tokens=1, seconds=1) as turn_budget:
            seen["turn"] = turn_budget
            manager.spawn("s", None, probe, noop)
        await manager.drain("s")

    asyncio.run(main())
    assert seen["budget"] is not None and seen["budget"] is not seen["turn"]
    assert seen["budget"].max_calls == 12


def test_收尾超时回滚并可再次排空(monkeypatch, caplog):
    monkeypatch.setattr(settings, "housekeeping_max_seconds", 0.05)

    async def main():
        manager = generation_lifecycle.HousekeepingManager()

        async def slow(db, sid, llm):
            await asyncio.sleep(10)

        async def never(db, sid, llm):
            raise AssertionError("超时后不应继续跑幕后推演")

        manager.spawn("s", None, slow, never)
        await manager.drain("s")
        assert "s" not in manager.tasks

    asyncio.run(main())
    assert "后台收尾达到时间上限" in caplog.text
