"""NpcRuntime：统一 NPC 出口的落库、广播与互动记忆回归。"""
import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Character, GameSession, Module
from app.services import session_service
from app.models.agent_trace import AgentTraceLog
from app.models.intent_history import IntentHistory
from app.services import agent_trace
from app.services.npc_runtime import NpcRuntime


def _db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'npc-runtime.db'}", connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed(db):
    module = Module(
        title="NPC 运行时", rule_system="coc",
        scenes=[{"id": "s1", "title": "门厅", "description": "一间门厅"}],
        npcs=[{"id": "butler", "name": "管家", "description": "年老的管家"}],
    )
    hero = Character(name="调查员", rule_system="coc", is_player=True, skills={})
    db.add_all([module, hero])
    db.commit()
    session = session_service.create_session(
        db, module.id, [{"character_id": hero.id, "role": "human", "is_primary": True}],
        creator_token="kp-token", kp_mode="ai",
    )
    return module, hero, session


def test_npc_reply_records_target_state(tmp_path, monkeypatch):
    db = _db(tmp_path)()
    module, hero, session = _seed(db)

    async def fake_respond(self, messages):
        return "我把门锁上了。"

    monkeypatch.setattr("app.services.npc_runtime.NPCAgent.respond", fake_respond)
    runtime = NpcRuntime(db, session.id, session, module, llm=object(), player_char=hero)
    asyncio.run(runtime.reply("butler", "玩家要进门", target="门", verb="lock"))
    from app.services import intent_ledger
    assert intent_ledger.state(runtime.game_session.world_state or {}, "门") == "locked"
    history = db.query(IntentHistory).filter(IntentHistory.session_id == session.id).all()
    assert history and history[-1].source == "npc"
    assert history[-1].verb == "lock"


def test_intent_history_filters_by_source_and_actor(tmp_path, monkeypatch):
    db = _db(tmp_path)()
    module, hero, session = _seed(db)

    async def fake_respond(self, messages):
        return "我锁上门。"

    monkeypatch.setattr("app.services.npc_runtime.NPCAgent.respond", fake_respond)
    runtime = NpcRuntime(db, session.id, session, module, llm=object(), player_char=hero)
    asyncio.run(runtime.reply("butler", "玩家要进门", target="门", verb="lock"))
    from app.services import world_intent

    npc_only = world_intent.list_for_session(db, session.id, source="npc")
    assert npc_only and all(i["source"] == "npc" for i in npc_only)
    assert not world_intent.list_for_session(db, session.id, source="combat")
    assert world_intent.list_for_session(db, session.id, actor="butler")
    assert not world_intent.list_for_session(db, session.id, actor="不存在")


def test_npc_runtime_times_out_and_traces(tmp_path, monkeypatch, caplog):
    db = _db(tmp_path)()
    module, hero, session = _seed(db)

    async def slow_respond(self, messages):
        import asyncio
        await asyncio.sleep(0.2)
        return "太晚了。"

    monkeypatch.setattr("app.services.npc_runtime.NPCAgent.respond", slow_respond)
    monkeypatch.setattr("app.services.npc_runtime.NPC_RESPONSE_TIMEOUT_SECONDS", 0.01)
    runtime = NpcRuntime(db, session.id, session, module, llm=object(), player_char=hero)
    with caplog.at_level("INFO", logger="agent.trace"):
        try:
            asyncio.run(runtime.reply("butler", "玩家推门进来"))
        except Exception as exc:
            assert exc.__class__.__name__ == "NpcResponseTimeout"
        else:
            raise AssertionError("应当超时")
    assert "agent_trace" in caplog.text
    assert "timeout" in caplog.text


def test_agent_trace_prune_keeps_recent(tmp_path):
    db = _db(tmp_path)()
    module, hero, session = _seed(db)
    from app.models.agent_trace import AgentTraceLog

    for i in range(6):
        db.add(AgentTraceLog(
            session_id=session.id, agent="npc", stage=str(i), outcome="ok",
            duration_ms=i, detail={"i": i},
        ))
    db.commit()
    removed = agent_trace.prune_for_session(db, session.id, keep=2)
    assert removed == 4
    assert db.query(AgentTraceLog).filter(AgentTraceLog.session_id == session.id).count() == 2


def test_npc_runtime_reply_persists_dialogue_and_memory(tmp_path, monkeypatch):
    db = _db(tmp_path)()
    module, hero, session = _seed(db)

    async def fake_respond(self, messages):
        return "欢迎光临。"

    monkeypatch.setattr("app.services.npc_runtime.NPCAgent.respond", fake_respond)
    runtime = NpcRuntime(db, session.id, session, module, llm=object(), player_char=hero)
    chunks, line = asyncio.run(runtime.reply("butler", "玩家推门进来"))

    assert line == "欢迎光临。"
    assert chunks and chunks[0].content == "欢迎光临。"
    events = session_service.get_session_events(db, session.id)
    dialogue = next(e for e in events if e.event_type == "dialogue")
    assert dialogue.actor_id == "butler" and dialogue.content == "欢迎光临。"
    memories = (db.get(GameSession, session.id).world_state or {}).get("npc_memory") or {}
    assert memories.get("butler", {}).get("interactions")
    traces = db.query(AgentTraceLog).filter(AgentTraceLog.session_id == session.id).all()
    assert any(t.agent == "npc" and t.outcome == "ok" for t in traces)
    listed = agent_trace.list_for_session(db, session.id)
    assert listed and listed[0]["agent"] == "npc"


def test_npc_意图前置不满足时只说台词不落对象账本(tmp_path, monkeypatch):
    db = _db(tmp_path)()
    module, hero, session = _seed(db)

    async def fake_respond(self, messages):
        return "我推门进去。"

    monkeypatch.setattr("app.services.npc_runtime.NPCAgent.respond", fake_respond)
    runtime = NpcRuntime(db, session.id, session, module, llm=object(), player_char=hero)
    asyncio.run(runtime.reply("butler", "玩家要进门", target="门", verb="lock"))
    from app.services import intent_ledger
    before = intent_ledger.state(runtime.game_session.world_state or {}, "门")
    assert before == "locked"
    count = db.query(IntentHistory).filter(IntentHistory.session_id == session.id).count()
    chunks, line = asyncio.run(runtime.reply("butler", "管家想进屋", target="门", verb="enter"))
    assert line == "我推门进去。" and chunks
    assert intent_ledger.state(runtime.game_session.world_state or {}, "门") == "locked"
    rows = db.query(IntentHistory).filter(IntentHistory.session_id == session.id).all()
    assert len(rows) == count + 1
    assert rows[-1].verb in ("", None) and not rows[-1].target
