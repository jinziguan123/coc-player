"""Agent 运行轨迹的落库契约。

背景：stage 曾经带默认空串，六个调用点一个都没传，于是 KP 面板那一列常年是一排「—」
（实测全表 14 条、零条有值）。这里把两件事钉住：start 必须收 stage；以及 stage 真的落库、
真的经 list_for_session 回到面板。
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.agent_trace import AgentTraceLog
from app.models.base import Base
from app.models.module import Module
from app.models.session import GameSession
from app.models.session_participant import SessionParticipant  # noqa: F401 — 注册建表
from app.services import agent_trace


@pytest.fixture
def db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'trace.db'}", connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        yield s


@pytest.fixture
def session(db):
    module = Module(title="轨迹测试模组", rule_system="coc")
    db.add(module)
    db.flush()
    gs = GameSession(module_id=module.id, status="active")
    db.add(gs)
    db.commit()
    return gs


def test_start_requires_stage():
    """必填是刻意的：谁忘了写会当场报错，而不是又留一列空数据等人去发现。"""
    with pytest.raises(TypeError):
        agent_trace.start("session-x", "planner")  # type: ignore[call-arg]


def test_stage_is_persisted_and_listed(db, session):
    trace = agent_trace.start(session.id, "planner", "规划本回合", db=db)
    agent_trace.finish(trace, outcome="ok")

    row = db.query(AgentTraceLog).filter(AgentTraceLog.session_id == session.id).one()
    assert row.stage == "规划本回合"

    listed = agent_trace.list_for_session(db, session.id)
    assert listed[0]["stage"] == "规划本回合"
