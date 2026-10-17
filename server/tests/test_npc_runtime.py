"""NpcRuntime：统一 NPC 出口的落库、广播与互动记忆回归。"""
import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Character, GameSession, Module
from app.services import session_service
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
