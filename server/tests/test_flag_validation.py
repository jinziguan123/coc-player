"""模型来源的剧情标志统一过白名单；真人 KP 的明确操作不受影响。"""
import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.ai.provider import ToolCall
from app.models import Base, Character, GameSession, Module  # noqa: F401
from app.services import entity_state, plot_progress, turn_effects
from app.services.kp_tool_loop import _build_kp_tool_executor


def _db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'f.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _seed(db):
    module = Module(
        title="m", rule_system="coc", npcs=[],
        scenes=[{"id": "cellar", "title": "地窖", "states": [{"when": ["cellar_flooded"], "description": "积水"}]}],
        triggers=[{"when": "管家被揭穿", "set_flags": ["butler_exposed"], "clear_flags": ["butler_trusted"]}],
    )
    pc = Character(name="亨利", rule_system="coc", is_player=True)
    db.add_all([module, pc]); db.flush()
    s = GameSession(module_id=module.id, player_character_id=pc.id, status="active",
                    current_scene_id="cellar", world_state={"flags": {"butler_trusted": True}})
    db.add(s); db.commit()
    return s, pc, module


def test_白名单口径(tmp_path):
    db = _db(tmp_path)
    s, _pc, module = _seed(db)
    flags = entity_state.active_flags(s)
    assert plot_progress.model_flag_rejection(module, flags, "butler_exposed", True) == ""
    assert plot_progress.model_flag_rejection(module, flags, "cellar_flooded", True) == ""   # 实体状态变体
    assert plot_progress.model_flag_rejection(module, flags, "made_up", True)
    assert plot_progress.model_flag_rejection(module, flags, "butler_trusted", False) == ""
    assert plot_progress.model_flag_rejection(module, flags, "butler_exposed", False)       # 未激活不清


def test_KP工具置编造标志被拒且不落库(tmp_path):
    db = _db(tmp_path)
    s, pc, module = _seed(db)
    execute = _build_kp_tool_executor(db, s.id, s, module, pc, [], None, ["", "", [], [], []])
    outcome = asyncio.run(execute(ToolCall(id="1", name="set_flag", arguments={"flag": "made_up"})))
    assert "已忽略" in str(outcome)
    db.expire_all()
    assert "made_up" not in entity_state.active_flags(db.get(GameSession, s.id))
    asyncio.run(execute(ToolCall(id="2", name="set_flag", arguments={"flag": "butler_exposed"})))
    db.expire_all()
    assert "butler_exposed" in entity_state.active_flags(db.get(GameSession, s.id))


def test_真人KP直接置标志不经白名单(tmp_path):
    db = _db(tmp_path)
    s, _pc, _module = _seed(db)
    turn_effects._exec_flag(db, s.id, s, "kp_private_note", True)
    db.expire_all()
    assert "kp_private_note" in entity_state.active_flags(db.get(GameSession, s.id))


def test_规划器标志与推进记录同事务_记录失败则标志全部回滚(tmp_path, monkeypatch):
    from app.ai.turn_planner import TurnPlan
    from app.services import session_service

    db = _db(tmp_path)
    s, _pc, module = _seed(db)
    plan = TurnPlan(scene_policy={"set_flags": ["butler_exposed"], "clear_flags": ["butler_trusted"]})
    real = session_service.add_event

    def boom(*a, **k):
        raise RuntimeError("记录写入失败")

    monkeypatch.setattr(session_service, "add_event", boom)

    async def run():
        return [c async for c in plot_progress.apply_planned_flags(db, s.id, s, module, plan)]

    assert asyncio.run(run()) == []          # fail-open：不阻断跑团
    db.expire_all()
    flags = entity_state.active_flags(db.get(GameSession, s.id))
    assert flags == {"butler_trusted"}       # 没有半截推进
    monkeypatch.setattr(session_service, "add_event", real)
    asyncio.run(run())
    db.expire_all()
    assert entity_state.active_flags(db.get(GameSession, s.id)) == {"butler_exposed"}
    notes = [e for e in session_service.get_session_events(db, s.id)
             if (e.metadata_ or {}).get("kind") == plot_progress.PLOT_PROGRESS_KIND]
    assert len(notes) == 1 and notes[0].metadata_["effect"]["kind"] == "plot_flags"
