"""重新生成路径的耗时打点：和常规回合一样，每段都要能在日志里对账。"""

import asyncio
import logging

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.services import chat_service, session_service
from tests.test_chat_service import _patch_runtime, _seed_session


@pytest.fixture
def db_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def test_重新生成有完整耗时打点(db_factory, monkeypatch, caplog):
    """此前点「重新生成」一条耗时日志都没有：planner 在 _run_generation 内部现跑、没打点，
    外层也没有 KP 叙事与合计。现在四段齐全（等收尾只在超过 0.5s 时才记）。"""
    _patch_runtime(monkeypatch, db_factory)

    async def fake_run_turn_planner(llm, messages):
        return chat_service.turn_planner.TurnPlan()

    async def fake_stream(kp, messages, result, npcs=None, **kwargs):
        result[0] = "管家垂下眼。"
        result[1] = result[0]
        yield chat_service._make_chunk("narration", result[0], actor_name="KP")

    async def fake_process(*args, **kwargs):
        if False:
            yield None

    monkeypatch.setattr(chat_service.turn_planner, "run_turn_planner", fake_run_turn_planner)
    monkeypatch.setattr(chat_service, "_stream_narration_filtered", fake_stream)
    monkeypatch.setattr(chat_service, "_process_commands", fake_process)

    db = db_factory()
    session_id = _seed_session(db)
    session_service.add_event(db, session_id, "action", "我搜查书桌", actor_name="测试角色")

    with caplog.at_level(logging.INFO, logger="app.services.turn_orchestrator"):
        asyncio.run(chat_service.run_regenerate_generation(session_id))

    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "耗时|planner（生成内）" in text
    assert "耗时|重新生成 KP 叙事" in text
    assert "耗时|重新生成合计" in text
