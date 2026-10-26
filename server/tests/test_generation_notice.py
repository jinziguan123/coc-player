"""整轮超时/预算耗尽的暂停事件持久化与生成范围内去重。"""
import asyncio

import pytest

from app import database
from app.ai import call_budget
from app.config import settings
from app.services import chat_event_writer, event_store, generation_manager, session_service
from tests.test_kp_tool_loop import db_factory as _db_factory, _seed

db_factory = _db_factory


@pytest.mark.parametrize("mode", ["timeout", "budget", "inner", "failed_inner"])
def test_预算暂停可重连读取_内外层只记一次_下一轮仍可记录(db_factory, monkeypatch, mode):
    db = db_factory()
    sid, *_ = _seed(db)
    monkeypatch.setattr(database, "SessionLocal", db_factory)
    monkeypatch.setattr(settings, "generation_max_seconds", 0.02)
    chunks = []
    monkeypatch.setattr(generation_manager.room_hub, "broadcast", lambda _sid, c: chunks.append(c))
    original = event_store.add_event
    attempts = []

    def add_event(*args, **kwargs):
        attempts.append(True)
        if mode == "failed_inner" and len(attempts) == 1:
            raise RuntimeError("内层提示写入失败")
        return original(*args, **kwargs)
    monkeypatch.setattr(event_store, "add_event", add_event)

    async def work():
        if mode == "timeout":
            await asyncio.Event().wait()
        if mode in {"inner", "failed_inner"}:
            with db_factory() as inner_db:
                chat_event_writer.persist_error_notice(inner_db, sid, call_budget.BUDGET_PAUSE_NOTICE)
        raise call_budget.BudgetExceeded("预算耗尽")

    async def run():
        manager = generation_manager.GenerationManager()
        for _ in range(2):
            await manager.start(sid, work())
            await asyncio.sleep(0)
            assert not manager.is_generating(sid)
    asyncio.run(run())
    with db_factory() as reader:
        saved = [e for e in session_service.get_session_events(reader, sid)
                 if e.content == call_budget.BUDGET_PAUSE_NOTICE]
        assert len(saved) == 2
        assert all(e.metadata_["kind"] == "generation_budget_paused" for e in saved)
        notices = [c for c in chunks if c.content == call_budget.BUDGET_PAUSE_NOTICE]
        assert [c.id for c in notices] == [e.id for e in saved]
    assert chunks[-1].type == "done"


def test_暂停说明落库失败仍通知并释放锁(db_factory, monkeypatch):
    db = db_factory()
    sid, *_ = _seed(db)
    monkeypatch.setattr(database, "SessionLocal", db_factory)
    def fail(*args, **kwargs):
        raise RuntimeError("数据库不可用")
    monkeypatch.setattr(event_store, "add_event", fail)
    chunks = []
    monkeypatch.setattr(generation_manager.room_hub, "broadcast", lambda _sid, c: chunks.append(c))
    async def work():
        raise call_budget.BudgetExceeded("预算耗尽")
    async def run():
        manager = generation_manager.GenerationManager()
        await manager.start(sid, work())
        await asyncio.sleep(0)
        assert not manager.is_generating(sid)
    asyncio.run(run())
    assert chunks[-1].type == "done"
    notice = next(c for c in chunks if c.content == call_budget.BUDGET_PAUSE_NOTICE)
    assert notice.id is None
