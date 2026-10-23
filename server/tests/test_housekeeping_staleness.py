"""后台收尾跨越模型等待的真实接线：等待期间另一数据库会话写入，旧结果不得覆盖新局面。"""
from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.models.character import Character
from app.models.event_log import EventLog
from app.models.module import Module
from app.models.session import GameSession
from app.models.session_participant import SessionParticipant  # noqa: F401 — 注册建表
from app.services import generation_housekeeping, session_service, world_memory


@pytest.fixture
def db_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'stale.db'}", connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine)
    engine.dispose()


class _InterleavingLLM:
    """模型等待期间执行 ``during``（模拟另一请求经独立会话写库），然后返回预置结果。"""

    def __init__(self, response: str, during):
        self.response = response
        self.during = during
        self.calls = 0

    async def complete(self, messages, **kw):
        self.calls += 1
        await asyncio.sleep(0)
        self.during()
        return self.response


def _seed_long(db, n=50):
    module = Module(
        title="快照模组", rule_system="coc",
        npcs=[{"id": "npc_butler", "name": "老管家", "secrets": "他藏了尸体"}],
        scenes=[{"id": "hall", "name": "大厅"}, {"id": "well", "name": "水井"}],
    )
    player = Character(name="亨利", rule_system="coc")
    db.add_all([module, player])
    db.flush()
    session = GameSession(
        module_id=module.id, player_character_id=player.id, status="active",
        current_scene_id="hall",
        world_state={"npc_memory": {"npc_butler": {"attitude": "neutral"}}},
    )
    db.add(session)
    db.flush()
    for i in range(1, n + 1):
        db.add(EventLog(
            session_id=session.id, sequence_num=i, event_type="narration",
            content=f"第{i}段旁白。" + "灯火在长廊尽头摇晃，墙上的影子跟着一同起伏，脚步声被地毯吃掉了大半。" * 13,
        ))
    db.commit()
    return session, module, player


_SUMMARY = json.dumps({
    "summary": "旧快照写出的梗概。",
    "npc_updates": {"npc_butler": {"attitude": "wary"}},
}, ensure_ascii=False)


def _write_world(factory, sid, key, value):
    def run():
        other = factory()
        s = other.get(GameSession, sid)
        s.world_state = {**(s.world_state or {}), key: value}
        other.commit()
        other.close()
    return run


def test_摘要等待期间世界状态被改则丢弃旧摘要(db_factory):
    db = db_factory()
    session, _m, _p = _seed_long(db)
    llm = _InterleavingLLM(_SUMMARY, _write_world(db_factory, session.id, "new_fact", "必须保留"))
    asyncio.run(generation_housekeeping._maybe_roll_story_summary(db, session.id, llm))
    assert llm.calls == 1
    check = db_factory()
    ws = check.get(GameSession, session.id).world_state
    assert ws["new_fact"] == "必须保留"
    assert "story_summary_seq" not in ws and "story_summary" not in ws
    assert ws["npc_memory"]["npc_butler"]["attitude"] == "neutral"   # 旧差量也不应用


def test_摘要等待期间仅追加新事件时照常推进且不吞新事件(db_factory):
    db = db_factory()
    session, _m, player = _seed_long(db)

    def append():
        other = db_factory()
        session_service.add_event(other, session.id, "action", "玩家抢先行动", actor_id=player.id)
        other.close()

    llm = _InterleavingLLM(_SUMMARY, append)
    asyncio.run(generation_housekeeping._maybe_roll_story_summary(db, session.id, llm))
    check = db_factory()
    ws = check.get(GameSession, session.id).world_state
    assert ws["story_summary_seq"] < 51          # 游标只覆盖被摘要的旧区间
    assert ws["npc_memory"]["npc_butler"]["attitude"] == "wary"
    events = session_service.get_session_events(check, session.id)
    assert events[-1].content == "玩家抢先行动"


def test_摘要等待期间旧区间被回滚则丢弃(db_factory):
    db = db_factory()
    session, _m, _p = _seed_long(db)

    def rollback_old_event():
        other = db_factory()
        other.query(EventLog).filter(
            EventLog.session_id == session.id, EventLog.sequence_num == 3,
        ).delete()
        other.commit()
        other.close()

    llm = _InterleavingLLM(_SUMMARY, rollback_old_event)
    asyncio.run(generation_housekeeping._maybe_roll_story_summary(db, session.id, llm))
    ws = db_factory().get(GameSession, session.id).world_state
    assert "story_summary_seq" not in ws


def test_章节合并等待期间世界状态变化则放弃且不覆盖(db_factory, monkeypatch):
    db = db_factory()
    session, _m, _p = _seed_long(db, n=3)
    chapters = [{"text": f"第{i}章", "from_seq": i, "to_seq": i} for i in range(1, 8)]
    session.world_state = {**session.world_state, world_memory.STORY_CHAPTERS_KEY: chapters}
    db.commit()
    llm = _InterleavingLLM("合并后的梗概", _write_world(db_factory, session.id, "flag_fresh", True))
    asyncio.run(generation_housekeeping._maybe_merge_chapters(db, session, llm))
    ws = db_factory().get(GameSession, session.id).world_state
    assert ws["flag_fresh"] is True
    assert [c["text"] for c in ws[world_memory.STORY_CHAPTERS_KEY]] == [c["text"] for c in chapters]


def test_章节合并无并发变更时正常落库(db_factory):
    db = db_factory()
    session, _m, _p = _seed_long(db, n=3)
    chapters = [{"text": f"第{i}章", "from_seq": i, "to_seq": i} for i in range(1, 8)]
    session.world_state = {**session.world_state, world_memory.STORY_CHAPTERS_KEY: chapters}
    db.commit()
    llm = _InterleavingLLM("合并后的梗概", lambda: None)
    asyncio.run(generation_housekeeping._maybe_merge_chapters(db, session, llm))
    ws = db_factory().get(GameSession, session.id).world_state
    assert ws[world_memory.STORY_CHAPTERS_KEY][0]["text"].startswith("合并后的梗概")


_BACKSTAGE = json.dumps({"events": [{
    "npc_id": "npc_butler", "action": "把尸体移到井里", "affected_scene": "well",
}]}, ensure_ascii=False)


def _seed_backstage(db):
    session, module, player = _seed_long(db, n=0)
    for i in range(7):
        session_service.add_event(db, session.id, "action", f"调查第{i}处", actor_id=player.id)
    return session, player


@pytest.mark.parametrize("change", ["world", "scene"])
def test_幕后推演等待期间局面变化则不落事件不推进游标(db_factory, change):
    db = db_factory()
    session, _p = _seed_backstage(db)

    def mutate():
        other = db_factory()
        s = other.get(GameSession, session.id)
        if change == "world":
            s.world_state = {**(s.world_state or {}), "flags": ["flag_new"]}
        else:
            s.current_scene_id = "well"
        other.commit()
        other.close()

    llm = _InterleavingLLM(_BACKSTAGE, mutate)
    asyncio.run(generation_housekeeping._maybe_run_backstage(db, session.id, llm))
    assert llm.calls == 1
    check = db_factory()
    kp_only = [e for e in session_service.get_session_events(check, session.id)
               if session_service.is_kp_only_event(e)]
    assert kp_only == []
    bs = (check.get(GameSession, session.id).world_state or {}).get("backstage") or {}
    assert int(bs.get("last_run_seq") or 0) == 0


def test_幕后推演等待期间仅追加事件时落库且游标停在评估点(db_factory):
    db = db_factory()
    session, player = _seed_backstage(db)

    def append():
        other = db_factory()
        session_service.add_event(other, session.id, "action", "并发行动", actor_id=player.id)
        other.close()

    llm = _InterleavingLLM(_BACKSTAGE, append)
    asyncio.run(generation_housekeeping._maybe_run_backstage(db, session.id, llm))
    check = db_factory()
    kp_only = [e for e in session_service.get_session_events(check, session.id)
               if session_service.is_kp_only_event(e)]
    assert len(kp_only) == 1
    bs = check.get(GameSession, session.id).world_state["backstage"]
    assert bs["last_run_seq"] == 7   # 并发追加的第 8 条留给下一轮
