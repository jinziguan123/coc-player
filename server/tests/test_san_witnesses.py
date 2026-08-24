"""SAN 的目睹者判定：在场 ≠ 目睹。

同一间屋里，甲掀开床单看见尸体、乙背对着在翻抽屉——乙没看见，凭什么掉 SAN？
等他被招呼过来、自己做出「过去看」这个动作，那一轮才轮到他。
所以 chars 缺省不是「在场全体」，而是「本轮真正行动过的人」。
"""

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Character, GameSession, Module, SessionParticipant  # noqa: F401
from app.services import session_service, turn_context, turn_effects


@pytest.fixture
def db_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'witness.db'}", connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed(db):
    """同一个场景里的两名真人角色：甲（主角）与乙。"""
    module = Module(
        title="鬼屋", rule_system="coc", npcs=[],
        scenes=[{"id": "house", "title": "老房子", "name": "老房子"}],
    )
    jia = Character(
        name="陈守一", rule_system="coc", is_player=True,
        base_attributes={}, skills={}, system_data={"sanity": {"current": 55, "max": 99}},
    )
    yi = Character(
        name="坂田桐时", rule_system="coc", is_player=True,
        base_attributes={}, skills={}, system_data={"sanity": {"current": 75, "max": 99}},
    )
    db.add(module); db.add_all([jia, yi]); db.flush()
    session = GameSession(
        module_id=module.id, player_character_id=jia.id, status="active",
        world_state={}, current_scene_id="house",
    )
    db.add(session); db.flush()
    db.add_all([
        SessionParticipant(
            session_id=session.id, character_id=char.id, role="human",
            is_primary=(index == 0), seat_order=index, claimed=True, ready=True,
        )
        for index, char in enumerate((jia, yi))
    ])
    db.commit()
    return db.get(GameSession, session.id), jia, yi


def _san_request_actors(db, session_id):
    return {
        event.metadata_["actor_name"]
        for event in session_service.get_session_events(db, session_id)
        if event.event_type == "system" and (event.metadata_ or {}).get("kind") == "san_check"
    }


def _turn(db, session_id, *actions: tuple[Character, str]) -> int:
    """摆一轮玩家输入，返回 pre_gen_seq（KP 生成前的最大序号）。"""
    session_service.add_event(db, session_id, "narration", "上一轮的旁白。", actor_name="KP")
    for char, content in actions:
        session_service.add_event(
            db, session_id, "action", content, actor_id=char.id, actor_name=char.name,
        )
    return session_service.get_next_sequence_num(db, session_id) - 1


def _fire_san(db, session, jia, yi, pre_gen_seq, **kv):
    payload = {"success_loss": "0", "failure_loss": "1d6", "source": "床单下的尸体", **kv}
    return asyncio.run(turn_effects._exec_san_check(
        db, session.id, session, payload, jia, [yi], pre_gen_seq=pre_gen_seq,
    ))


def test_only_the_one_who_acted_checks(db_factory):
    """甲掀床单，乙在翻抽屉：只有甲检定。"""
    db = db_factory()
    session, jia, yi = _seed(db)
    pre = _turn(db, session.id, (jia, "我掀开床单看看下面。"))

    _fire_san(db, session, jia, yi, pre)

    assert _san_request_actors(db, session.id) == {jia.name}


def test_teammate_checks_once_he_comes_over_to_look(db_factory):
    """乙被招呼过来、自己做出行动 → 这一轮轮到他；同源去重不会让甲再检一次。"""
    db = db_factory()
    session, jia, yi = _seed(db)
    first = _turn(db, session.id, (jia, "我掀开床单看看下面。"))
    _fire_san(db, session, jia, yi, first)
    # 甲那次待投骰落库后，模拟他投完（写进幂等台账），再进入下一轮
    turn_effects._mark_san_checked(db, session, "床单下的尸体", jia.id)
    before = session_service.get_next_sequence_num(db, session.id)

    second = _turn(db, session.id, (yi, "我走过去，也低头看了一眼。"))
    _fire_san(db, session, jia, yi, second)

    later = {
        event.metadata_["actor_name"]
        for event in session_service.get_session_events(db, session.id)
        if event.event_type == "system"
        and (event.metadata_ or {}).get("kind") == "san_check"
        and (event.sequence_num or 0) >= before
    }
    assert later == {yi.name}


def test_bystander_who_only_spoke_is_still_involved(db_factory):
    """本轮开口说话也算参与——「那是什么？」问完自然会看过去。"""
    db = db_factory()
    session, jia, yi = _seed(db)
    session_service.add_event(db, session.id, "narration", "上一轮的旁白。", actor_name="KP")
    session_service.add_event(
        db, session.id, "action", "我掀开床单。", actor_id=jia.id, actor_name=jia.name,
    )
    session_service.add_event(
        db, session.id, "dialogue", "那底下是什么？", actor_id=yi.id, actor_name=yi.name,
    )
    pre = session_service.get_next_sequence_num(db, session.id) - 1

    _fire_san(db, session, jia, yi, pre)

    assert _san_request_actors(db, session.id) == {jia.name, yi.name}


def test_explicit_present_token_still_covers_everyone(db_factory):
    """怪物破门而入这种躲不开的：KP 写 chars=在场 → 在场全体照检。"""
    db = db_factory()
    session, jia, yi = _seed(db)
    pre = _turn(db, session.id, (jia, "我掀开床单看看下面。"))

    _fire_san(db, session, jia, yi, pre, chars="在场")

    assert _san_request_actors(db, session.id) == {jia.name, yi.name}


def test_named_chars_win_over_who_acted(db_factory):
    """KP 点名了就按点名的来——它读过自己写的叙事，比「谁行动过」准。"""
    db = db_factory()
    session, jia, yi = _seed(db)
    pre = _turn(db, session.id, (jia, "我掀开床单看看下面。"))

    _fire_san(db, session, jia, yi, pre, chars=yi.name)

    assert _san_request_actors(db, session.id) == {yi.name}


def test_falls_back_to_present_party_without_turn_context(db_factory):
    """拿不到本轮上下文（真人 KP 手动发起等）→ 回落在场全体，宁可多检也不静默漏检。"""
    db = db_factory()
    session, jia, yi = _seed(db)
    _turn(db, session.id, (jia, "我掀开床单看看下面。"))

    _fire_san(db, session, jia, yi, None)

    assert _san_request_actors(db, session.id) == {jia.name, yi.name}


def test_turn_actor_chars_ignores_events_after_generation(db_factory):
    """KP 叙事之后的 action/dialogue 是 NPC 与系统的产物，不算玩家参与。"""
    db = db_factory()
    session, jia, yi = _seed(db)
    pre = _turn(db, session.id, (jia, "我掀开床单看看下面。"))
    session_service.add_event(db, session.id, "narration", "床单滑落……", actor_name="KP")
    session_service.add_event(
        db, session.id, "dialogue", "别看！", actor_id=yi.id, actor_name=yi.name,
    )

    involved = turn_context.turn_actor_chars(db, session.id, [jia, yi], pre)

    assert [char.name for char in involved] == [jia.name]
