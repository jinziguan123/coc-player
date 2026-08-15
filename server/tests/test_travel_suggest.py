"""「要不要去某处」建议卡：KP 主动建议 + 玩家嘴上说了却没动的确定性兜底。

这张卡**不改任何状态**，只是给玩家挂一个可点的按钮；点了才走既有的暂存式「前往」，
不点就什么都不发生。所以本文件的断言重点是「别挂出点了会失败的卡」和「别反复问」。
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (  # noqa: F401 — 注册全部表
    Base,
    Character,
    EventLog,
    GameSession,
    Module,
    SessionParticipant,
)
from app.services import session_service, turn_effects, world_memory

SCENES = [
    {"id": "hall", "title": "门厅", "connections": ["library"]},
    {"id": "library", "title": "图书馆", "connections": ["hall"], "keywords": ["图书馆"]},
    # 阁楼—地窖自成一个连通分量：与门厅确实走不通（若给 attic 留空 connections，
    # find_scene_path 会按「作者没建边、无拓扑可循」保守放行，测不出拒绝）。
    {"id": "attic", "title": "阁楼", "connections": ["cellar"], "keywords": ["阁楼"]},
    {"id": "cellar", "title": "地窖", "connections": ["attic"], "keywords": ["地窖"]},
]


@pytest.fixture
def seeded(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'ts.db'}", connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    module = Module(title="M", rule_system="coc", npcs=[], scenes=SCENES)
    hero = Character(name="主角", rule_system="coc", is_player=True)
    db.add_all([module, hero]); db.commit()
    session = session_service.create_session(
        db, module.id, [{"character_id": hero.id, "is_primary": True}],
    )
    session.current_scene_id = "hall"
    session.navigation.visited_scenes = ["hall"]
    db.commit()
    yield db, module, session, hero
    db.close()


def test_suggest_emits_card_without_moving_anyone(seeded):
    """建议只挂卡，绝不改位置——这是它和 [SCENE_CHANGE] 的根本分别。"""
    db, module, session, hero = seeded
    chunks, note = turn_effects.travel_suggest_event(
        db, session.id, session, module, "library", "门缝里透出灯光",
    )

    assert len(chunks) == 1
    assert "图书馆" in chunks[0].content and "门缝里透出灯光" in chunks[0].content
    assert "已给玩家挂出" in note
    # 位置纹丝不动
    assert session.current_scene_id == "hall"
    assert session_service.get_char_location(session, hero.id) == "hall"

    ev = session_service.get_session_events(db, session.id)[-1]
    assert ev.metadata_["kind"] == "travel_suggest"
    assert ev.metadata_["scene_id"] == "library"
    assert ev.metadata_["scene_name"] == "图书馆"


def test_never_suggests_disconnected_scene(seeded):
    """不连通的地方不许挂卡：点下去 /travel 必然 400，等于给玩家一个坏按钮。"""
    db, module, session, _hero = seeded
    chunks, note = turn_effects.travel_suggest_event(
        db, session.id, session, module, "attic",
    )
    assert chunks == []
    assert "不连通" in note


def test_never_suggests_current_scene(seeded):
    db, module, session, _hero = seeded
    chunks, note = turn_effects.travel_suggest_event(
        db, session.id, session, module, "hall",
    )
    assert chunks == []
    assert "已身处" in note


def test_unresolvable_reference_is_skipped(seeded):
    db, module, session, _hero = seeded
    chunks, note = turn_effects.travel_suggest_event(
        db, session.id, session, module, "并不存在的地方",
    )
    assert chunks == []
    assert "无法解析" in note


def test_same_place_is_asked_only_once(seeded):
    """同一个地方整局只问一次：反复弹卡是这类功能最烦人的形态，而玩家真想去时大地图一直都在。"""
    db, module, session, _hero = seeded
    first, _ = turn_effects.travel_suggest_event(db, session.id, session, module, "library")
    second, note = turn_effects.travel_suggest_event(db, session.id, session, module, "library")

    assert len(first) == 1
    assert second == []
    assert "已经建议过" in note
    assert world_memory.travel_suggested(session.world_state, "library")


def test_scene_name_reference_resolves(seeded):
    """KP 常写场景名而不是 id，得认。"""
    db, module, session, _hero = seeded
    chunks, _note = turn_effects.travel_suggest_event(db, session.id, session, module, "图书馆")
    assert len(chunks) == 1


def test_record_travel_suggestion_is_pure(seeded):
    """纯函数：不改入参，重复记为 no-op。"""
    ws = {"visited_scenes": ["hall"]}
    out = world_memory.record_travel_suggestion(ws, "library")
    assert "travel_suggested" not in ws            # 入参没被就地改写
    assert out["travel_suggested"] == ["library"]
    assert world_memory.record_travel_suggestion(out, "library")["travel_suggested"] == ["library"]
    assert world_memory.record_travel_suggestion(out, "")["travel_suggested"] == ["library"]


def _say(db, session, hero, text):
    """按真实流程把玩家发言落成事件——地点的「已知」判定扫的是事件，不是传进来的字符串。"""
    session_service.add_event(
        db, session.id, "action", text, actor_id=hero.id, actor_name=hero.name,
    )
    return text


def test_spoken_intent_triggers_card(seeded):
    """玩家嘴上说了「去图书馆」却没点大地图 → 确定性挂卡（不走 LLM）。

    这是本功能最常撞上的形态：场景切换一向要玩家显式发起（杜绝「说句话就被自动搬走」），
    于是玩家打完字、KP 顺着叙述了一段，人却还留在原地。
    """
    from app.services import turn_orchestrator

    db, module, session, hero = seeded
    text = _say(db, session, hero, "我们去图书馆看看吧")
    chunks = turn_orchestrator._spoken_travel_intent(db, session.id, session, module, text)

    assert len(chunks) == 1
    assert chunks[0].metadata["scene_id"] == "library"
    assert session.current_scene_id == "hall"      # 依旧不搬人


def test_location_intent_guard_clears_plan_for_tentative_travel(seeded):
    """生成前位置硬闸：『之后可能去B』必须清掉 plan.scene_change 并给出硬约束。"""
    from app.ai.turn_planner import TurnPlan
    from app.services import planned_effects, turn_orchestrator

    db, module, session, hero = seeded
    session.navigation.visited_scenes = ["hall", "library"]
    db.commit()
    _say(db, session, hero, "我们之后可能去图书馆看看")
    events = session_service.get_session_events(db, session.id)
    plan = TurnPlan()
    plan.scene_policy.scene_change = "library"

    guard = turn_orchestrator._location_intent_guard(
        db, session.id, session, module, hero, events, plan,
    )

    assert plan.scene_policy.scene_change is None
    assert "门厅" in guard and "图书馆" in guard and "最高优先级" in guard
    assert planned_effects._explicit_player_movement(
        db, session.id, module, hero, "library",
    ) is False


def test_location_intent_guard_allows_explicit_movement(seeded):
    """『我走进图书馆』是确定性移动，位置硬闸不得误伤。"""
    from app.ai.turn_planner import TurnPlan
    from app.services import turn_orchestrator

    db, module, session, hero = seeded
    session.navigation.visited_scenes = ["hall", "library"]
    db.commit()
    _say(db, session, hero, "我走进图书馆")
    events = session_service.get_session_events(db, session.id)
    plan = TurnPlan()
    plan.scene_policy.scene_change = "library"

    guard = turn_orchestrator._location_intent_guard(
        db, session.id, session, module, hero, events, plan,
    )

    assert guard == ""
    assert plan.scene_policy.scene_change == "library"


def test_spoken_intent_skips_disconnected_place(seeded):
    """提到了走不通的地方（阁楼自成一片）→ 不挂卡：点下去 /travel 必然 400。"""
    from app.services import turn_orchestrator

    db, module, session, hero = seeded
    text = _say(db, session, hero, "阁楼上会不会有东西")
    assert turn_orchestrator._spoken_travel_intent(db, session.id, session, module, text) == []


def test_spoken_intent_skips_current_scene(seeded):
    """说的是自己脚下这处 → 不问。"""
    from app.services import turn_orchestrator

    db, module, session, hero = seeded
    text = _say(db, session, hero, "门厅里再找找")
    assert turn_orchestrator._spoken_travel_intent(db, session.id, session, module, text) == []


def test_spoken_intent_only_reads_player_text(seeded):
    """只认玩家自己这一轮的文本：KP 的叙述里提一嘴某地就弹卡，正是要避免的 nag。"""
    from app.services import turn_orchestrator

    db, module, session, _hero = seeded
    session_service.add_event(
        db, session.id, "narration", "走廊尽头是图书馆的双开门。", actor_name="KP",
    )
    assert turn_orchestrator._spoken_travel_intent(db, session.id, session, module, "") == []


def test_capability_advertised_only_when_there_is_somewhere_to_go(seeded):
    """有多处地点才广告「建议前往」能力：只有一处的本子广告了也只会诱导 KP 发无效指令。

    这段刻意不在静态手册里——那份手册已贴着 token 预算上限（见 test_kp_rulebook），
    再塞一条就会挤占下游注入内容的余量。
    """
    from app.ai import context as ctx

    db, module, session, hero = seeded
    system = ctx.build_kp_context(session, module, hero, [])[0]["content"]
    assert "TRAVEL_SUGGEST" in system

    module.scenes = [SCENES[0]]
    only_one = ctx.build_kp_context(session, module, hero, [])[0]["content"]
    assert "TRAVEL_SUGGEST" not in only_one


# 常暗之箱实测复现：六节车厢一字排开，先头车厢（关键词含「驾驶室」）只能经 2 号车厢抵达。
# 玩家说「我们要带着这家伙一起进驾驶室」，2 号车厢却还没去过——此前系统照样挂出建议卡，
# 点下去还会放行，而抵达叙述写明「途经不停留、不触发事件」= 无视 2 号车厢的怪物穿过去。
TRAIN = [
    {"id": "s6", "title": "6号车厢", "connections": ["s5"], "keywords": ["6号车厢"]},
    {"id": "s5", "title": "5号车厢", "connections": ["s6", "s4"], "keywords": ["5号车厢"]},
    {"id": "s4", "title": "4号车厢", "connections": ["s5", "s3"], "keywords": ["4号车厢"]},
    {"id": "s3", "title": "3号车厢", "connections": ["s4", "s2"], "keywords": ["3号车厢"]},
    {"id": "s2", "title": "2号车厢", "connections": ["s3", "head"], "keywords": ["2号车厢"]},
    {"id": "head", "title": "先头车厢", "connections": ["s2"], "keywords": ["先头车厢", "驾驶室"]},
]


@pytest.fixture
def train(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'train.db'}", connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    module = Module(title="常暗之箱", rule_system="coc", npcs=[], scenes=TRAIN)
    hero = Character(name="江户川龙牙", rule_system="coc", is_player=True)
    db.add_all([module, hero]); db.commit()
    session = session_service.create_session(
        db, module.id, [{"character_id": hero.id, "is_primary": True}],
    )
    session.current_scene_id = "s3"
    # 一路从 6 号走到 3 号；2 号车厢有怪物挡着，没进去过
    session.navigation.visited_scenes = ["s6", "s5", "s4", "s3"]
    db.commit()
    yield db, module, session, hero
    db.close()


def test_no_suggestion_for_place_behind_an_unvisited_leg(train):
    """先头车厢在图上与 3 号车厢连通，但唯一通路 2 号车厢没去过 → 不挂卡。"""
    db, module, session, _hero = train
    chunks, note = turn_effects.travel_suggest_event(db, session.id, session, module, "head")
    assert chunks == []
    assert "2号车厢" in note      # 点名是哪一段挡着，KP 才知道该改建议去中间那处

    # 玩家嘴上说「进驾驶室」同样不该弹卡——这正是实测碰到的那一下
    from app.services import turn_orchestrator

    session_service.add_event(
        db, session.id, "action", "我们要带着这家伙一起进驾驶室，至于后面怎么说再议",
        actor_id=_hero_id(session), actor_name="江户川龙牙",
    )
    assert turn_orchestrator._spoken_travel_intent(
        db, session.id, session, module, "我们要带着这家伙一起进驾驶室",
    ) == []


def _hero_id(session):
    return session.player_character_id


def test_suggestion_allowed_for_the_next_leg(train):
    """挡路的那一节本身可以建议——一段一段走过去正是我们要引导的。"""
    db, module, session, _hero = train
    chunks, _note = turn_effects.travel_suggest_event(db, session.id, session, module, "s2")
    assert len(chunks) == 1
    assert chunks[0].metadata["scene_id"] == "s2"


def test_far_place_becomes_reachable_once_the_leg_is_visited(train):
    """2 号车厢去过之后，先头车厢就是正常的多跳目标了。"""
    db, module, session, _hero = train
    session.navigation.visited_scenes = ["s6", "s5", "s4", "s3", "s2"]
    db.commit()
    chunks, _note = turn_effects.travel_suggest_event(db, session.id, session, module, "head")
    assert len(chunks) == 1


# ── 此路不通（KP 显式标记）─────────────────────────────────────────────
#
# 「途经必须去过」堵不住这一类：队伍进过 2 号车厢又退回来，怪物还在那儿，
# 但 2 号车厢已经算「去过」了，路线又通了。connections 只说物理相连，
# 说不了「现在能不能过」——这是只有 KP 知道的故事事实，得由它标出来。

def test_blocked_scene_is_not_a_valid_leg(train):
    """标记为过不去之后，取道该处的更远目标不再可达，且拒绝理由带上 KP 给的原因。"""
    db, module, session, _hero = train
    session.navigation.visited_scenes = ["s6", "s5", "s4", "s3", "s2"]
    db.commit()
    # 先确认没标记时是通的
    assert len(turn_effects.travel_suggest_event(db, session.id, session, module, "head")[0]) == 1

    session.world_state = {**session.world_state, "travel_suggested": []}
    turn_effects.set_path_block(
        db, session.id, session, module, "s2", "那东西还堵在车厢里", blocked=True,
    )
    chunks, note = turn_effects.travel_suggest_event(db, session.id, session, module, "head")
    assert chunks == []
    assert "那东西还堵在车厢里" in note


def test_blocked_scene_is_still_a_valid_destination(train):
    """封的是「借道穿过去」，不是「不许去」——走进危险是玩家的自由。"""
    db, module, session, _hero = train
    session.navigation.visited_scenes = ["s6", "s5", "s4", "s3"]
    db.commit()
    turn_effects.set_path_block(db, session.id, session, module, "s2", "怪物", blocked=True)

    assert session_service.find_scene_path(
        module, "s3", "s2", via_allowed=session_service.passable_scene_ids(session),
    ) == ["s3", "s2"]


def test_unblock_restores_the_route(train):
    db, module, session, _hero = train
    session.navigation.visited_scenes = ["s6", "s5", "s4", "s3", "s2"]
    db.commit()
    turn_effects.set_path_block(db, session.id, session, module, "s2", "怪物", blocked=True)
    assert "s2" not in session_service.passable_scene_ids(session)

    turn_effects.set_path_block(db, session.id, session, module, "s2", "", blocked=False)
    assert "s2" in session_service.passable_scene_ids(session)


def test_block_state_is_pure_and_idempotent():
    ws = {"visited_scenes": ["a"]}
    out = world_memory.record_block(ws, "s2", "怪物")
    assert "blocked_scenes" not in ws                 # 入参没被就地改写
    assert world_memory.blocked_scenes(out) == {"s2": "怪物"}
    again = world_memory.record_block(out, "s2", "换了个原因")
    assert world_memory.blocked_scenes(again) == {"s2": "换了个原因"}
    assert world_memory.blocked_scenes(world_memory.record_unblock(again, "s2")) == {}
    assert world_memory.record_unblock(ws, "不存在") == ws


def test_kp_context_lists_currently_blocked_paths(train):
    """封着的路要摆进 KP 上下文：不给它这份清单，威胁解除时它就想不起来解封，
    那条路会一直断着——这是「靠 KP 记得解」最容易塌的地方。"""
    from app.ai import context as ctx

    db, module, session, hero = train
    turn_effects.set_path_block(
        db, session.id, session, module, "s2", "那东西还堵在车厢里", blocked=True,
    )
    system = ctx.build_kp_context(session, module, hero, [])[0]["content"]
    assert "当前封着的路" in system
    assert "2号车厢" in system and "那东西还堵在车厢里" in system
    assert "UNBLOCK_PATH" in system


def test_winning_a_fight_auto_unblocks_that_scene(train):
    """打赢就自动解封：忘一次 unblock，那条路就永久断着且没人知道为什么。"""
    from app.services import combat_service

    db, module, session, _hero = train
    session.current_scene_id = "s2"
    db.commit()
    turn_effects.set_path_block(db, session.id, session, module, "s2", "怪物", blocked=True)

    combat_service._end_combat(db, session.id, {"participants": [], "round": 1}, "players_win")
    db.refresh(session)
    assert world_memory.blocked_scenes(session.world_state) == {}


def test_losing_a_fight_keeps_the_block(train):
    """打输/逃走不解封——那东西还在。"""
    from app.services import combat_service

    db, module, session, _hero = train
    session.current_scene_id = "s2"
    db.commit()
    turn_effects.set_path_block(db, session.id, session, module, "s2", "怪物", blocked=True)

    combat_service._end_combat(db, session.id, {"participants": [], "round": 1}, "players_defeated")
    db.refresh(session)
    assert "s2" in world_memory.blocked_scenes(session.world_state)
