"""分头行动下的检定归属：谁该跟着投骰，以及投骰卡上「因何而检」。

复现的问题：队伍分成两处，A 组目睹恐怖触发 SAN_CHECK，B 组人在另一个场景却也一起掉了 SAN。
根因是群体检定的候选域一直是「全队」，而分头时各组叙事合并成一段文本统一处理，
指令本身不带出处——只看主角在哪，无从区分这条指令是哪一组的事。
"""

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.ai.turn_planner import SanityPolicy, TurnPlan
from app.models import Base, Character, GameSession, Module, SessionParticipant  # noqa: F401
from app.services import chat_service as cs
from app.services import dice_runtime, kp_tool_loop, session_service, turn_effects

HOUSE = "科比特的老房子"
ASYLUM = "罗克斯伯里疗养院"


@pytest.fixture
def db_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'split.db'}", connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed(db, *, human_seats=True):
    """两处场景、四名玩家角色：主角与桐时在老房子，知微与伊芙琳在疗养院。"""
    module = Module(
        title="鬼屋", rule_system="coc", npcs=[],
        scenes=[
            {"id": "house", "title": HOUSE, "name": HOUSE},
            {"id": "asylum", "title": ASYLUM, "name": ASYLUM},
        ],
    )
    chars = [
        Character(
            name=name, rule_system="coc", is_player=True,
            base_attributes={"敏捷": 50}, skills={"侦查": 50},
            system_data={"sanity": {"current": san, "max": 99}},
        )
        for name, san in (
            ("陈守一", 55), ("坂田桐时", 75), ("林知微", 67), ("伊芙琳·哈特", 50),
        )
    ]
    db.add(module); db.add_all(chars); db.flush()
    hero, mate_house, mate_asylum_a, mate_asylum_b = chars
    session = GameSession(
        module_id=module.id, player_character_id=hero.id, status="active",
        world_state={}, current_scene_id="house",
    )
    db.add(session); db.flush()
    if human_seats:
        # 全席真人：SAN 一律挂「待投骰」，测试无需触碰 LLM 续写路径。
        db.add_all([
            SessionParticipant(
                session_id=session.id, character_id=char.id, role="human",
                is_primary=(index == 0), seat_order=index, claimed=True, ready=True,
            )
            for index, char in enumerate(chars)
        ])
    db.commit()
    for char, scene in (
        (hero, "house"), (mate_house, "house"),
        (mate_asylum_a, "asylum"), (mate_asylum_b, "asylum"),
    ):
        session_service.set_char_location(db, session.id, char.id, scene)
    return db.get(GameSession, session.id), module, chars


def _run(coro):
    async def collect():
        return [chunk async for chunk in coro]
    return asyncio.run(collect())


def _san_requests(db, session_id):
    return [
        event for event in session_service.get_session_events(db, session_id)
        if event.event_type == "system" and (event.metadata_ or {}).get("kind") == "san_check"
    ]


# ── 目睹者候选域 ──────────────────────────────────────────────────────────


def test_san_check_default_targets_only_the_present_party(db_factory):
    """缺省 chars 时只对与主角同场景的人发起——另一处的队友没看见，凭什么掉 SAN。"""
    db = db_factory()
    session, _module, chars = _seed(db)
    hero, mate_house, *asylum = chars

    chunks, _descs, pending = asyncio.run(turn_effects._exec_san_check(
        db, session.id, session,
        {"success_loss": "0", "failure_loss": "1d6", "source": "床底伸出的手指"},
        hero, chars[1:],
    ))

    assert pending and chunks
    actors = {event.metadata_["actor_name"] for event in _san_requests(db, session.id)}
    assert actors == {hero.name, mate_house.name}
    assert not actors & {char.name for char in asylum}


def test_san_check_named_targets_cannot_reach_another_scene(db_factory):
    """KP 把别处场景的队友写进 chars 也不算数：目睹是 SAN 的唯一依据。"""
    db = db_factory()
    session, _module, chars = _seed(db)
    hero, mate_house, mate_asylum_a, _ = chars

    asyncio.run(turn_effects._exec_san_check(
        db, session.id, session,
        {"failure_loss": "1d6", "source": "腐尸", "chars": f"{hero.name}/{mate_asylum_a.name}"},
        hero, chars[1:],
    ))

    actors = {event.metadata_["actor_name"] for event in _san_requests(db, session.id)}
    assert actors == {hero.name}
    assert mate_asylum_a.name not in actors
    assert mate_house.name not in actors  # 未点名的同场景队友也不该被拉进来


def test_san_check_falls_back_to_whole_party_without_locations(db_factory):
    """没有位置追踪的旧会话（单场景常态）行为不变：全队一起检。"""
    db = db_factory()
    session, _module, chars = _seed(db)
    session.navigation.party_locations = {}
    db.commit()
    hero = chars[0]

    asyncio.run(turn_effects._exec_san_check(
        db, session.id, db.get(GameSession, session.id),
        {"failure_loss": "1d6", "source": "腐尸"}, hero, chars[1:],
    ))

    actors = {event.metadata_["actor_name"] for event in _san_requests(db, session.id)}
    assert actors == {char.name for char in chars}


# ── 指令归组（合并文本 → 发出它的那一组）────────────────────────────────


def _groups():
    return [
        {"scene_id": "house", "label": HOUSE, "members": ["陈守一", "坂田桐时"]},
        {"scene_id": "asylum", "label": ASYLUM, "members": ["林知微", "伊芙琳·哈特"]},
    ]


def test_group_scope_resolver_maps_position_to_its_group(db_factory):
    db = db_factory()
    _session, _module, chars = _seed(db)
    hero = chars[0]
    text = (
        f"[GROUP: scene={HOUSE}]\n屋里安静得不像话。\n"
        f"[GROUP: scene={ASYLUM}]\n病房里那个人影还站在原地。\n"
    )
    resolve = kp_tool_loop._group_scope_resolver(text, _groups(), hero, chars[1:])

    assert [c.name for c in resolve(text.index("屋里"))] == ["陈守一", "坂田桐时"]
    assert [c.name for c in resolve(text.index("病房"))] == ["林知微", "伊芙琳·哈特"]

    # 首个组界标记之前的位置无从判断 → 回落到本轮聚焦组；没有聚焦组则交回缺省（在场全体）
    stray = "开场白。\n" + text
    assert [
        c.name for c in kp_tool_loop._group_scope_resolver(
            stray, _groups(), hero, chars[1:], focus_group_label=ASYLUM,
        )(0)
    ] == ["林知微", "伊芙琳·哈特"]
    assert kp_tool_loop._group_scope_resolver(stray, _groups(), hero, chars[1:])(0) is None


def test_group_scope_resolver_is_noop_without_split(db_factory):
    db = db_factory()
    _session, _module, chars = _seed(db)
    resolve = kp_tool_loop._group_scope_resolver("随便什么文本", None, chars[0], chars[1:])
    assert resolve(0) is None


def test_san_check_stays_in_the_group_that_saw_the_horror(db_factory):
    """端到端：疗养院那一组的 [SAN_CHECK] 不该波及老房子里的人（含主角）。"""
    db = db_factory()
    session, module, chars = _seed(db)
    hero = chars[0]
    kp_text = (
        f"[GROUP: scene={HOUSE}]\n屋内维持着一种近乎刻意的安静。\n"
        f"[GROUP: scene={ASYLUM}]\n病床下伸出一截青灰色的手指。\n"
        "[SAN_CHECK: success_loss=0, failure_loss=1d6, source=病床下的手指, "
        "reason=病床下伸出的那截青灰色手指]\n"
    )

    _run(kp_tool_loop._process_commands(
        db, session.id, kp_text, module, hero, session, llm=None,
        teammates=chars[1:], scene_groups=_groups(),
    ))

    requests = _san_requests(db, session.id)
    assert {event.metadata_["actor_name"] for event in requests} == {"林知微", "伊芙琳·哈特"}
    for char in chars:
        db.refresh(char)
    assert [char.system_data["sanity"]["current"] for char in chars] == [55, 75, 67, 50]


def test_group_dice_check_stays_in_its_group(db_factory):
    """群检同理：老房子里的一声响，疗养院的人听不见。"""
    db = db_factory()
    session, module, chars = _seed(db)
    hero = chars[0]
    kp_text = (
        f"[GROUP: scene={ASYLUM}]\n走廊里很安静。\n"
        f"[GROUP: scene={HOUSE}]\n墙根传来极轻的窸窣声。\n"
        "[DICE_CHECK: skill=聆听, char=在场, reason=墙根那阵极轻的窸窣声]\n"
    )

    _run(kp_tool_loop._process_commands(
        db, session.id, kp_text, module, hero, session, llm=None,
        teammates=chars[1:], scene_groups=_groups(),
    ))

    requests = [
        event for event in session_service.get_session_events(db, session.id)
        if (event.metadata_ or {}).get("check_request")
    ]
    assert {event.metadata_["actor_name"] for event in requests} == {"陈守一", "坂田桐时"}


def test_sanity_guard_ignores_another_groups_terror(db_factory):
    """确定性 SAN 守卫也不能拿别组的恐怖叙事，给主角这一组补检定。"""
    db = db_factory()
    session, module, chars = _seed(db)
    hero = chars[0]
    pre = session_service.get_next_sequence_num(db, session.id) - 1
    other = session_service.add_event(
        db, session.id, "narration", "病床下伸出一截腐烂的手指，指节还在抽动。",
        actor_name="KP",
    )
    session_service.set_event_group(db, other, ASYLUM)

    plan = TurnPlan(sanity=SanityPolicy(trigger=True, source="病床下的腐尸", failure_loss="1d6"))
    chunks = _run(cs._ensure_planned_sanity(
        db, session.id, db.get(GameSession, session.id), hero, chars[1:], plan, pre,
        module=module,
    ))

    assert chunks == []
    assert _san_requests(db, session.id) == []


# ── 投骰卡上的「因何而检」────────────────────────────────────────────────


def test_check_request_carries_reason(db_factory):
    db = db_factory()
    session, module, chars = _seed(db)
    hero = chars[0]

    asyncio.run(turn_effects._exec_dice_check(
        db, session.id, session, module,
        {"skill": "侦查", "reason": "抽屉合不严，缝里露出一角纸"}, hero, chars[1:],
    ))

    request = next(
        event for event in session_service.get_session_events(db, session.id)
        if (event.metadata_ or {}).get("check_request")
    )
    assert request.metadata_["reason"] == "抽屉合不严，缝里露出一角纸"


def test_dice_check_reason_does_not_fall_back_to_source(db_factory):
    """source 是检定针对的**对象**，玩家可能还没发现它——不能拿它当缘由印在卡上剧透。"""
    db = db_factory()
    session, module, chars = _seed(db)
    hero = chars[0]

    asyncio.run(turn_effects._exec_dice_check(
        db, session.id, session, module,
        {"skill": "侦查", "source": "书桌暗格"}, hero, chars[1:],
    ))

    request = next(
        event for event in session_service.get_session_events(db, session.id)
        if (event.metadata_ or {}).get("check_request")
    )
    assert request.metadata_["reason"] == ""


def test_san_check_reason_falls_back_to_source_text(db_factory):
    db = db_factory()
    session, _module, chars = _seed(db)
    hero = chars[0]

    asyncio.run(turn_effects._exec_san_check(
        db, session.id, session,
        {"failure_loss": "1d6", "source": "床底伸出的手指"}, hero, chars[1:],
    ))

    assert {event.metadata_["reason"] for event in _san_requests(db, session.id)} == {
        "床底伸出的手指",
    }


def test_san_reason_restores_mechanism_key_to_module_trigger(db_factory):
    """幂等键是机器串，摆到玩家面前得还原成模组写的那句话。"""
    module = Module(
        title="常暗之箱", rule_system="coc", npcs=[],
        scenes=[{
            "id": "scene_5",
            "events": [{"trigger": "阅读报纸时", "kind": "san_check", "san_loss": "0/1"}],
        }],
    )
    assert dice_runtime._san_source_label(module, "scene:scene_5:san:0") == "阅读报纸时"
    assert dice_runtime._san_source_label(module, "墓室腐尸") == "墓室腐尸"
    assert dice_runtime._san_source_label(module, "scene:missing:san:3") == ""
