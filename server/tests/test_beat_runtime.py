"""事件效果执行器（beat_runtime）的确定性结算测试。"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Character, GameSession, Module  # noqa: F401 — 注册全部表
from app.services import beat_runtime


@pytest.fixture
def db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'beat.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _seed(db):
    module = Module(
        title="测试模组", rule_system="coc",
        scenes=[{"id": "scene_1", "title": "起点"}, {"id": "scene_2", "title": "终点"}],
        endings=[{"id": "end_a", "name": "结局A"}],
        npcs=[{"id": "npc_1", "name": "老人"}],
        clues=[{"id": "clue_1", "name": "线索"}],
    )
    hero = Character(name="主角", rule_system="coc", is_player=True)
    db.add_all([module, hero])
    db.commit()
    game = GameSession(
        module_id=module.id, player_character_id=hero.id,
        status="active", current_scene_id="scene_1",
    )
    db.add(game)
    db.commit()
    return module, hero, game


def test_event_effects_apply_state_and_mark_seen(db):
    module, hero, game = _seed(db)
    event = {
        "id": "ev_1", "title": "转折",
        "effects": [
            {"type": "set_flag", "flag": "door_open"},
            {"type": "goto", "target": "scene_2"},
            {"type": "ending", "ending_id": "end_a"},
            {"type": "reveal_clue", "clue_id": "clue_1"},
            {"type": "narration", "text": "门开了。"},
        ],
    }
    note = beat_runtime.execute_event(db, game.id, game, module, event, hero, [], seq=3)
    db.refresh(game)
    assert "door_open" in (game.world_state or {}).get("flags", {})
    assert game.current_scene_id == "scene_2"
    assert (game.world_state or {})["ending_reached"]["id"] == "end_a"
    assert "clue_1" in ((game.world_state or {}).get("clue_ledger") or {})
    assert "ev_1" in (game.world_state or {}).get("flow_seen", [])
    assert "系统已结算" in note and "门开了" in note


def test_check_and_sanity_are_deferred_not_rolled(db):
    module, hero, game = _seed(db)
    event = {
        "id": "ev_2", "title": "检定",
        "effects": [
            {"type": "check", "skill": "侦查", "on_success": [], "on_failure": []},
            {"type": "sanity", "loss": "0/1"},
        ],
    }
    note = beat_runtime.execute_event(db, game.id, game, module, event, hero, [], seq=4)
    assert "检定" in note and "SAN" in note
    # 检定结果不能由 beat 直接伪造：不扣 SAN、不改 HP
    assert not (game.world_state or {}).get("ending_reached")


def test_execute_effects_does_not_mark_event_seen(db):
    module, hero, game = _seed(db)
    note = beat_runtime.execute_effects(
        db, game.id, game, module,
        [{"type": "set_flag", "flag": "branch_flag"}],
        hero, [],
    )
    db.refresh(game)
    assert "branch_flag" in (game.world_state or {}).get("flags", {})
    assert not (game.world_state or {}).get("flow_seen")
    assert "branch_flag" in note


def test_check_branch_effects_can_be_applied_directly(db):
    module, hero, game = _seed(db)
    note = beat_runtime.execute_effects(
        db, game.id, game, module,
        [{"type": "goto", "target": "scene_2"},
         {"type": "reveal_clue", "clue_id": "clue_1"}],
        hero, [],
        event_title="检定分支",
    )
    db.refresh(game)
    assert game.current_scene_id == "scene_2"
    assert "clue_1" in ((game.world_state or {}).get("clue_ledger") or {})
    assert "scene_2" in note


def test_goto_with_unknown_target_is_ignored(db):
    module, hero, game = _seed(db)
    event = {"id": "ev_3", "effects": [{"type": "goto", "target": "nowhere"}]}
    beat_runtime.execute_event(db, game.id, game, module, event, hero, [], seq=5)
    db.refresh(game)
    assert game.current_scene_id == "scene_1"
