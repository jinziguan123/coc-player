"""本局累计 token 消耗：contextvar 累加 + accumulate 纯函数 + tracked 落库。不调 LLM。"""

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.ai import usage_tracker
from app.models import Base, Character, GameSession, Module, SessionStats  # noqa: F401


def test_accumulate_is_monotonic_pure():
    su = usage_tracker.accumulate(None, {"prompt_tokens": 100, "completion_tokens": 20,
                                         "total_tokens": 120, "calls": 2})
    su = usage_tracker.accumulate(su, {"prompt_tokens": 50, "completion_tokens": 10,
                                       "total_tokens": 60, "calls": 1})
    assert su == {
        "prompt_tokens": 150, "completion_tokens": 30, "reasoning_tokens": 0,
        "total_tokens": 180, "cache_read_tokens": 0, "cache_write_tokens": 0,
        "calls": 3,
    }


def test_accumulate_does_not_mutate_input():
    su0 = {"total_tokens": 0, "calls": 0}
    su1 = usage_tracker.accumulate(su0, {"total_tokens": 5, "calls": 1})
    assert su0["total_tokens"] == 0 and su1["total_tokens"] == 5


def test_add_snapshot_scoped_to_task():
    async def run():
        usage_tracker._acc.set(usage_tracker._zero())
        usage_tracker.add({"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4})
        usage_tracker.add({"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9})
        usage_tracker.add(None)          # 无效 usage 忽略
        return usage_tracker.snapshot()
    snap = asyncio.run(run())
    assert snap == {"prompt_tokens": 10, "completion_tokens": 3, "reasoning_tokens": 0,
                    "total_tokens": 13, "cache_read_tokens": 0, "cache_write_tokens": 0,
                    "calls": 2}


def test_add_without_accumulator_is_ignored():
    async def run():
        usage_tracker.add({"total_tokens": 999})   # 无 begin/tracked → 忽略
        return usage_tracker.snapshot()
    assert asyncio.run(run()) == {"prompt_tokens": 0, "completion_tokens": 0,
                                  "reasoning_tokens": 0, "total_tokens": 0,
                                  "cache_read_tokens": 0, "cache_write_tokens": 0, "calls": 0}


@pytest.fixture
def db_session(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'u.db'}", connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    TS = sessionmaker(bind=engine)
    monkeypatch.setattr("app.database.SessionLocal", TS, raising=False)
    return TS


def _seed(TS) -> str:
    db = TS()
    module = Module(title="M", rule_system="coc", npcs=[], scenes=[])
    pc = Character(name="调查员", rule_system="coc", is_player=True)
    db.add_all([module, pc]); db.flush()
    gs = GameSession(module_id=module.id, player_character_id=pc.id, status="active", world_state={})
    db.add(gs); db.commit()
    sid = gs.id
    db.close()
    return sid


def test_tracked_persists_session_usage_and_accumulates(db_session):
    sid = _seed(db_session)

    async def gen(n):
        # 模拟一次生成里若干 LLM 子调用各自上报 usage
        usage_tracker.add({"prompt_tokens": n, "completion_tokens": 1, "total_tokens": n + 1})

    asyncio.run(usage_tracker.tracked(sid, gen(100)))
    asyncio.run(usage_tracker.tracked(sid, gen(50)))

    su = db_session().get(SessionStats, sid).session_usage
    assert su["total_tokens"] == (101 + 51) and su["calls"] == 2


def test_tracked_records_usage_even_on_exception(db_session):
    sid = _seed(db_session)

    async def boom():
        usage_tracker.add({"prompt_tokens": 30, "completion_tokens": 5, "total_tokens": 35})
        raise RuntimeError("生成中断")

    with pytest.raises(RuntimeError):
        asyncio.run(usage_tracker.tracked(sid, boom()))
    su = db_session().get(SessionStats, sid).session_usage
    assert su["total_tokens"] == 35 and su["calls"] == 1   # 半截生成也计入
