import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, GameSession, Module
from app.services import world_memory
from app.services.npc_state import NpcState


@pytest.fixture
def db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'npc-state.db'}", connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    yield db
    db.close()


def test_load_module_npc_state_and_memory(db):
    module = Module(
        title="状态测试", rule_system="coc",
        scenes=[{"id": "s1", "title": "门厅"}],
        npcs=[{
            "id": "butler", "name": "管家", "description": "老管家",
            "personality": "克制", "secrets": ["知道地下室"],
            "initial_location": "s1",
        }],
    )
    db.add(module)
    db.commit()
    session = GameSession(
        module_id=module.id, current_scene_id="s1", status="active",
        world_state={},
    )
    db.add(session)
    db.commit()
    session.world_state = world_memory.record_npc_interaction(
        {}, "butler", 1, "玩家在门厅向他问好",
    )
    db.commit()

    state = NpcState.load(module, session, "butler")
    assert state.name == "管家"
    assert state.location == "s1"
    assert "知道地下室" in state.persona_text()
    assert "玩家在门厅" in state.memory


def test_unknown_npc_falls_back_to_id_without_crashing(db):
    module = Module(title="空", rule_system="coc", scenes=[], npcs=[])
    session = GameSession(
        module_id="m", current_scene_id="s1", status="active", world_state={},
    )
    state = NpcState.load(module, session, "ghost")
    assert state.name == "ghost"
    assert state.memory == ""
