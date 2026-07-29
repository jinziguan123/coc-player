"""确定性 SAN 守卫：planner 裁定本轮目睹恐怖 → 引擎确定性发理智检定，不靠 KP 记得。"""

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.ai.turn_planner import SanityPolicy, TurnPlan
from app.models import Base, Character, GameSession, Module, SessionParticipant  # noqa: F401
from app.services import chat_service as cs
from app.services import dice_runtime, session_service


@pytest.fixture
def db_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'san.db'}", connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed(db):
    module = Module(title="M", rule_system="coc", npcs=[], scenes=[])
    pc = Character(name="龙牙", rule_system="coc", is_player=True,
                   base_attributes={}, skills={},
                   system_data={"sanity": {"current": 60, "max": 99}})
    db.add_all([module, pc]); db.flush()
    s = GameSession(module_id=module.id, player_character_id=pc.id, status="active", world_state={})
    db.add(s); db.commit()
    return s.id, pc


def _run(coro):
    async def collect():
        return [c async for c in coro]
    return asyncio.run(collect())


# ── plan schema ──

def test_plan_parses_sanity_field():
    plan = TurnPlan.model_validate({"sanity": {"trigger": True, "source": "墓室腐尸", "failure_loss": "1d6"}})
    assert plan.sanity.trigger is True and plan.sanity.source == "墓室腐尸"


def test_plan_sanity_sentence_shape_falls_back():
    # 模型把 sanity 写成一句话 → 退默认，不连累整份计划
    assert TurnPlan.model_validate({"sanity": "无恐怖"}).sanity.trigger is False


def test_build_message_carries_sanity():
    from app.ai.turn_planner import build_turn_plan_message
    msg = build_turn_plan_message(TurnPlan(sanity=SanityPolicy(trigger=True, source="怪物")))
    assert "sanity" in msg["content"] and "怪物" in msg["content"]


# ── 确定性守卫 ──

def test_guard_fires_san_when_planner_triggers(db_factory, monkeypatch):
    db = db_factory(); sid, pc = _seed(db)
    pre = session_service.get_next_sequence_num(db, sid) - 1
    session_service.add_event(db, sid, "narration", "手电照出一具腐尸。", actor_name="KP")
    plan = TurnPlan(sanity=SanityPolicy(trigger=True, source="墓室腐尸", success_loss="0", failure_loss="1d6"))
    chunks = _run(cs._ensure_planned_sanity(db, sid, db.get(GameSession, sid), pc, [], plan, pre))
    assert chunks                                   # 补发了 SAN 待投请求
    db.refresh(pc)
    assert pc.system_data["sanity"]["current"] == 60  # 玩家投骰前不扣 SAN
    evs = session_service.get_session_events(db, sid)
    assert any(
        e.event_type == "system" and (e.metadata_ or {}).get("kind") == "san_check"
        for e in evs
    )


def test_guard_skips_when_san_already_rolled_this_turn(db_factory):
    db = db_factory(); sid, pc = _seed(db)
    pre = session_service.get_next_sequence_num(db, sid) - 1
    # 模拟 KP 本轮已自行掷过 SAN
    session_service.add_event(db, sid, "dice", "龙牙｜理智检定", actor_name="系统", metadata={"skill": "SAN"})
    plan = TurnPlan(sanity=SanityPolicy(trigger=True, source="腐尸"))
    chunks = _run(cs._ensure_planned_sanity(db, sid, db.get(GameSession, sid), pc, [], plan, pre))
    assert chunks == []                             # 幂等跳过，不重复扣
    db.refresh(pc)
    assert pc.system_data["sanity"]["current"] == 60


def test_guard_noop_when_trigger_false(db_factory):
    db = db_factory(); sid, pc = _seed(db)
    pre = session_service.get_next_sequence_num(db, sid) - 1
    plan = TurnPlan(sanity=SanityPolicy(trigger=False))
    assert _run(cs._ensure_planned_sanity(db, sid, db.get(GameSession, sid), pc, [], plan, pre)) == []


def test_guard_skips_sanity_without_terror_evidence(db_factory):
    """只有异响/灯光等普通环境描写时，planner 的误触发不能凭空补 SAN。"""
    db = db_factory(); sid, pc = _seed(db)
    pre = session_service.get_next_sequence_num(db, sid) - 1
    session_service.add_event(db, sid, "narration", "六号车厢方向传来三次异响，灯光闪烁。", actor_name="KP")
    plan = TurnPlan(sanity=SanityPolicy(trigger=True, source="七号车厢的异响"))
    chunks = _run(cs._ensure_planned_sanity(
        db, sid, db.get(GameSession, sid), pc, [], plan, pre,
    ))
    assert chunks == []
    assert not any(
        e.event_type == "dice" and (e.metadata_ or {}).get("skill") == "SAN"
        for e in session_service.get_session_events(db, sid)
    )


def test_scene_mechanism_overrides_generic_sanity_loss(db_factory, monkeypatch):
    """实际进入场景时，优先使用模组明文 1/1d4，而不是 planner 默认 1d6。"""
    db = db_factory()
    module = Module(
        title="列车", rule_system="coc", npcs=[],
        scenes=[
            {"id": "car6", "title": "六号车厢"},
            {
                "id": "car7", "title": "七号车厢",
                "events": [{
                    "trigger": "进入七号车厢", "kind": "san_check", "san_loss": "1/1d4",
                }],
            },
        ],
    )
    pc = Character(
        name="龙牙", rule_system="coc", is_player=True,
        system_data={"sanity": {"current": 60, "max": 99}},
    )
    db.add_all([module, pc]); db.flush()
    session = GameSession(
        module_id=module.id, player_character_id=pc.id, status="active",
        world_state={}, current_scene_id="car6",
    )
    db.add(session); db.commit()
    pre = session_service.get_next_sequence_num(db, session.id) - 1
    session_service.set_char_location(db, session.id, pc.id, "car7")
    session_service.add_event(
        db, session.id, "action", "（前往：七号车厢）", actor_id=pc.id, actor_name=pc.name,
    )
    plan = TurnPlan(sanity=SanityPolicy(trigger=True, source="七号车厢", failure_loss="1d6"))
    chunks = _run(cs._ensure_planned_sanity(
        db, session.id, db.get(GameSession, session.id), pc, [], plan, pre, module=module,
    ))
    assert chunks
    san_request = next(
        e for e in session_service.get_session_events(db, session.id)
        if e.event_type == "system" and (e.metadata_ or {}).get("kind") == "san_check"
    )
    assert san_request.metadata_["success_loss"] == "1"
    assert san_request.metadata_["failure_loss"] == "1d4"


def test_scene_san_source_alias_resolves_to_stable_key():
    module = Module(
        title="常暗之箱", rule_system="coc", npcs=[],
        scenes=[{
            "id": "scene_5",
            "events": [{
                "trigger": "阅读报纸时", "kind": "san_check", "san_loss": "0/1",
            }],
        }],
    )
    assert dice_runtime._canonical_san_source(
        module, "scene_5", "报纸新闻",
    ) == "scene:scene_5:san:0"


def test_scene_san_reaches_teammates_who_read_source_later(db_factory, monkeypatch):
    """同一恐怖信息稍后传给队友时，只补检尚未接触过该来源的角色。"""
    db = db_factory()
    module = Module(
        title="常暗之箱", rule_system="coc", npcs=[],
        scenes=[{
            "id": "scene_5",
            "title": "5号车厢",
            "events": [{
                "trigger": "阅读报纸时", "kind": "san_check", "san_loss": "0/1",
            }],
        }],
    )
    hero = Character(
        name="江户川龙牙", rule_system="coc", is_player=True,
        system_data={"sanity": {"current": 42, "max": 99}},
    )
    ally_a = Character(
        name="林知微", rule_system="coc", is_player=True,
        system_data={"sanity": {"current": 74, "max": 99}},
    )
    ally_b = Character(
        name="相马直树", rule_system="coc", is_player=True,
        system_data={"sanity": {"current": 56, "max": 99}},
    )
    db.add_all([module, hero, ally_a, ally_b]); db.flush()
    source_key = "scene:scene_5:san:0"
    session = GameSession(
        module_id=module.id,
        player_character_id=hero.id,
        status="active",
        current_scene_id="scene_5",
        # 旧存档仍保存模型自由命名的来源；运行时只做兼容比较，不迁移存档。
        world_state={"san_checked": [f"报纸新闻|{hero.id}"]},
    )
    db.add(session); db.flush()
    db.add_all([
        SessionParticipant(
            session_id=session.id, character_id=hero.id, role="human",
            is_primary=True, claimed=True, ready=True,
        ),
        SessionParticipant(
            session_id=session.id, character_id=ally_a.id, role="ai",
            seat_order=1, claimed=True, ready=True,
        ),
        SessionParticipant(
            session_id=session.id, character_id=ally_b.id, role="ai",
            seat_order=2, claimed=True, ready=True,
        ),
    ])
    db.commit()
    session_service.add_event(db, session.id, "narration", "报纸暂时收在龙牙手中。", actor_name="KP")
    pre = session_service.get_next_sequence_num(db, session.id) - 1
    session_service.add_event(
        db, session.id, "action", "我把报纸递给另外二人，让他们看看。",
        actor_id=hero.id, actor_name=hero.name,
    )
    session_service.add_event(
        db, session.id, "dialogue", "我接过报纸，读完标题和日期。",
        actor_id=ally_a.id, actor_name=ally_a.name,
    )
    session_service.add_event(
        db, session.id, "dialogue", "明天的报纸？这个日期根本不对。",
        actor_id=ally_b.id, actor_name=ally_b.name,
    )
    # 模拟 KP 已先替主角结算：结构化守卫不能因为本轮已有一张 SAN 卡就整体退出，
    # 仍须依靠稳定来源键补齐遗漏队友。
    already_settled = session_service.add_event(
        db, session.id, "dice", "江户川龙牙｜理智检定",
        actor_name="系统", metadata={"skill": "SAN", "actor": hero.name},
    )
    monkeypatch.setattr("app.rules.coc.checks.roll_percentile", lambda: 1)

    chunks = _run(cs._ensure_planned_sanity(
        db,
        session.id,
        db.get(GameSession, session.id),
        hero,
        [ally_a, ally_b],
        None,
        pre,
        module=module,
    ))

    assert chunks
    san_events = [
        event for event in session_service.get_session_events(db, session.id)
        if event.event_type == "dice" and (event.metadata_ or {}).get("skill") == "SAN"
        and (event.sequence_num or 0) > (already_settled.sequence_num or 0)
    ]
    assert {event.metadata_["actor"] for event in san_events} == {"林知微", "相马直树"}
    db.refresh(session)
    checked = set((session.world_state or {}).get("san_checked") or [])
    assert checked == {
        f"报纸新闻|{hero.id}",
        f"{source_key}|{ally_a.id}",
        f"{source_key}|{ally_b.id}",
    }


def test_check_continuation_fires_san_via_run_kp_turn(db_factory, monkeypatch):
    """检定后续写(sanity_guard=True)：KP 漏发 SAN，但叙事后现跑 planner 裁定目睹恐怖 → 确定性补发。

    复现问题二：恐怖由『侦查检定成功』才揭示，回合起点的 plan 看不到；本修复在叙事之后补跑
    planner（此时上下文已含刚揭示的恐怖）驱动确定性 SAN 守卫。
    """
    db = db_factory(); sid, pc = _seed(db)
    gs = db.get(GameSession, sid)
    module = db.get(Module, gs.module_id)
    async def _fake_stream(kp, messages, res, **kw):
        res[0] = "手电照亮了那具扭曲的尸体，面部中央裂开一道缝……"   # 恐怖描写，但**不发 [SAN_CHECK]**
        res[1] = res[0]
        for _ in ():
            yield ""   # 空异步生成器

    async def _fake_planner(llm, messages):
        return TurnPlan(sanity=SanityPolicy(
            trigger=True, source="扭曲的尸体", success_loss="0", failure_loss="1d6"))

    async def _noop_finish(db, sid, llm):
        return None

    monkeypatch.setattr(cs, "KPAgent", lambda llm: object())
    monkeypatch.setattr(cs, "get_llm", lambda: object())
    monkeypatch.setattr(cs, "get_fast_llm", lambda: object())
    monkeypatch.setattr(cs, "_stream_narration_filtered", _fake_stream)
    monkeypatch.setattr(cs, "build_kp_context", lambda *a, **k: [{"role": "system", "content": "x"}])
    monkeypatch.setattr(cs, "_module_excerpts_for_context", lambda *a, **k: [])
    monkeypatch.setattr(cs.turn_planner, "run_turn_planner", _fake_planner)
    monkeypatch.setattr(cs, "_finish_generation", _noop_finish)

    asyncio.run(cs._run_kp_turn(db, sid, gs, module, pc, [], "续写", sanity_guard=True))

    evs = session_service.get_session_events(db, sid)
    assert any(
        e.event_type == "system" and (e.metadata_ or {}).get("kind") == "san_check"
        for e in evs
    )
    db.refresh(pc)
    assert pc.system_data["sanity"]["current"] == 60  # 等玩家投骰后再扣


def test_check_continuation_no_guard_when_flag_off(db_factory, monkeypatch):
    """默认 sanity_guard=False（普通 KP 续写）：即便 planner 会触发也不补跑、不发 SAN。"""
    db = db_factory(); sid, pc = _seed(db)
    gs = db.get(GameSession, sid)
    module = db.get(Module, gs.module_id)
    ran = {"planner": False}

    async def _fake_stream(kp, messages, res, **kw):
        res[0] = res[1] = "一段平静的旁白。"
        for _ in ():
            yield ""

    async def _fake_planner(llm, messages):
        ran["planner"] = True
        return TurnPlan(sanity=SanityPolicy(trigger=True))

    monkeypatch.setattr(cs, "KPAgent", lambda llm: object())
    monkeypatch.setattr(cs, "get_llm", lambda: object())
    monkeypatch.setattr(cs, "get_fast_llm", lambda: object())
    monkeypatch.setattr(cs, "_stream_narration_filtered", _fake_stream)
    monkeypatch.setattr(cs, "build_kp_context", lambda *a, **k: [{"role": "system", "content": "x"}])
    monkeypatch.setattr(cs, "_module_excerpts_for_context", lambda *a, **k: [])
    monkeypatch.setattr(cs.turn_planner, "run_turn_planner", _fake_planner)

    async def _noop_finish(db, sid, llm):
        return None
    monkeypatch.setattr(cs, "_finish_generation", _noop_finish)

    asyncio.run(cs._run_kp_turn(db, sid, gs, module, pc, [], "续写"))   # 无 sanity_guard
    assert ran["planner"] is False                                     # 没有多跑 planner
    evs = session_service.get_session_events(db, sid)
    assert not any((e.metadata_ or {}).get("skill") == "SAN" for e in evs)
