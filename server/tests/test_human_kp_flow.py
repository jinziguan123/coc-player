"""真人 KP 补齐 AI KP 的关键能力：记账 / 封路 / 建议前往 / NPC 代演、HP 下拉与骰式、
待投检定撤销与代投、无名 SAN 不被同轮吞、私有台账、检定申请待办、旁白改写撤回、
全 AI 席自动队友、参谋读笔记、战斗中操控敌人。"""

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import chat as chat_api
from app.api import kp as kp_api
from app.api.chat import kp_action
from app.models import Base, Character, EventLog, GameSession, Module, SessionParticipant
from app.schemas.session import KpActionRequest
from app.services import (
    combat_service, human_kp_actions, human_kp_desk, human_kp_service, session_service,
)
from app.services.chat_service import execute_human_kp_action
from app.services.room_hub import room_hub


@pytest.fixture
def db_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'human-kp-flow.db'}", connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed(db, *, with_ai_mate: bool = False):
    module = Module(
        title="真人 KP 流程", description="", rule_system="coc",
        scenes=[{"id": "s1", "title": "门厅", "exits": ["s2"]}, {"id": "s2", "title": "地窖"}],
        npcs=[{"id": "guard", "name": "守门人", "attributes": {"DEX": 90, "CON": 50, "SIZ": 50},
               "skills": {"格斗(斗殴)": 40}, "weapon": "徒手格斗"}],
        clues=[{"id": "c1", "title": "染血的信"}, {"id": "c2", "title": "地窖钥匙"}],
    )
    hero = Character(
        name="调查员", rule_system="coc", is_player=True,
        base_attributes={"DEX": 10}, skills={"侦查": 60},
        system_data={"hitPoints": {"current": 12, "max": 12}, "sanity": {"current": 60, "max": 99}},
    )
    db.add_all([module, hero])
    mate = None
    if with_ai_mate:
        mate = Character(
            name="AI 队友", rule_system="coc", is_player=False, base_attributes={"DEX": 5},
            system_data={"hitPoints": {"current": 10, "max": 10}, "sanity": {"current": 60, "max": 99}},
        )
        db.add(mate)
    db.commit()
    seats = [{"character_id": hero.id, "role": "human", "is_primary": True}]
    if mate is not None:
        seats.append({"character_id": mate.id, "role": "ai"})
    session = session_service.create_session(
        db, module.id, seats, creator_token="kp-token", kp_mode="human",
    )
    if not session_service.lobby_gaps(db, session.id):
        session_service.start_game(db, session.id, "kp-token")
    db.refresh(session)
    return module, hero, mate, session


def _run(db, session, module, action, payload):
    return asyncio.run(execute_human_kp_action(db, session.id, session, module, action, payload))


# ── 1. 新入口 ────────────────────────────────────────────────────────────

def test_mark_seen_records_one_or_many_clues(db_factory):
    db = db_factory()
    module, _hero, _mate, session = _seed(db)
    _chunks, note = _run(db, session, module, "mark_seen", {"clues": ["c1", "c2"]})
    assert "c1" in note and "c2" in note
    db.refresh(session)
    assert {"c1", "c2"} <= set((session.world_state or {}).get("clue_ledger") or {})

    with pytest.raises(ValueError, match="未知线索"):
        _run(db, session, module, "mark_seen", {"clue": "nope"})
    with pytest.raises(ValueError, match="至少填一个"):
        _run(db, session, module, "mark_seen", {})


def test_block_and_unblock_path(db_factory):
    db = db_factory()
    module, _hero, _mate, session = _seed(db)
    _chunks, note = _run(db, session, module, "block_path", {"scene_id": "s2", "reason": "塌方"})
    assert "地窖" in note
    db.refresh(session)
    assert "s2" in str((session.world_state or {}).get("blocked_paths") or session.world_state)
    _chunks, note = _run(db, session, module, "unblock_path", {"scene_id": "地窖"})
    assert "恢复通行" in note
    with pytest.raises(ValueError, match="找不到"):
        _run(db, session, module, "block_path", {"scene_id": "火星"})


def test_travel_suggest_hangs_card_once(db_factory):
    db = db_factory()
    module, _hero, _mate, session = _seed(db)
    chunks, _note = _run(db, session, module, "travel_suggest", {"scene_id": "s2", "reason": "楼下有动静"})
    assert chunks[0].as_wire()["metadata"]["kind"] == "travel_suggest"
    with pytest.raises(ValueError, match="已经建议过"):
        _run(db, session, module, "travel_suggest", {"scene_id": "s2"})


def test_npc_act_resolves_npc_by_name_and_requires_trigger(db_factory, monkeypatch):
    db = db_factory()
    module, _hero, _mate, session = _seed(db)
    seen = {}

    async def _fake(db_, sid, gs, mod, llm, player, npc_id, trigger):
        seen.update(npc_id=npc_id, trigger=trigger)
        return [], "滚出去。"

    import app.ai.llm_factory as llm_factory
    monkeypatch.setattr(llm_factory, "get_llm", lambda: object())
    monkeypatch.setattr(human_kp_actions, "_exec_npc_act", _fake)
    _chunks, note = _run(db, session, module, "npc_act", {"npc_id": "守门人", "trigger": "有人闯门"})
    assert seen == {"npc_id": "guard", "trigger": "有人闯门"}
    assert "AI 代演" in note and "滚出去" in note
    with pytest.raises(ValueError, match="触发情境"):
        _run(db, session, module, "npc_act", {"npc_id": "guard"})
    with pytest.raises(ValueError, match="模组里的 NPC"):
        _run(db, session, module, "npc_act", {"npc_id": "路人甲", "trigger": "x"})


# ── 2. HP：下拉目标、空目标报错、骰式 ─────────────────────────────────────

def test_hp_change_requires_explicit_target(db_factory):
    db = db_factory()
    module, _hero, _mate, session = _seed(db)
    with pytest.raises(ValueError, match="请选择"):
        _run(db, session, module, "hp_change", {"delta": "-3"})
    with pytest.raises(ValueError, match="找不到这个角色"):
        _run(db, session, module, "hp_change", {"target": "调查", "delta": "-3"})   # 子串不再算数


def test_hp_change_rolls_dice_expression_via_ruleset(db_factory, monkeypatch):
    db = db_factory()
    module, hero, _mate, session = _seed(db)
    import app.rules.dice as dice
    monkeypatch.setattr(dice.random, "randint", lambda a, b: 4)
    _chunks, note = _run(db, session, module, "hp_change",
                         {"target": f"character:{hero.id}", "delta": "-1d6+1", "reason": "刀伤"})
    db.refresh(hero)
    assert hero.system_data["hitPoints"]["current"] == 7
    assert "1D6+1=5" in note
    with pytest.raises(ValueError, match="整数或骰式"):
        _run(db, session, module, "hp_change", {"target": f"character:{hero.id}", "delta": "-abc"})
    _run(db, session, module, "hp_change", {"target": f"character:{hero.id}", "delta": "+2"})
    db.refresh(hero)
    assert hero.system_data["hitPoints"]["current"] == 9


def _start_combat(db, session, module):
    _run(db, session, module, "start_combat", {"enemies": "守门人"})
    db.expire_all()
    return combat_service.get_combat(db.get(GameSession, session.id))


def test_hp_change_on_npc_combatant_and_kill_ends_combat(db_factory):
    db = db_factory()
    module, _hero, _mate, session = _seed(db)
    state = _start_combat(db, session, module)
    enemy = next(p for p in state["initiative"] if p["side"] == "enemy")
    _chunks, _note = _run(db, session, module, "hp_change",
                          {"target": f"combatant:{enemy['id']}", "delta": "-1"})
    db.expire_all()
    state = combat_service.get_combat(db.get(GameSession, session.id))
    assert next(p for p in state["initiative"] if p["id"] == enemy["id"])["hp"] == enemy["hp"] - 1

    chunks, _note = _run(db, session, module, "hp_change",
                         {"target": f"combatant:{enemy['id']}", "delta": "-99"})
    assert any(c.as_wire()["type"] == "combat_end" for c in chunks)


# ── 3. 待投检定：撤销 / 群检收束 / 代投 ──────────────────────────────────

def _hang_check(db, session, hero, check_id, **extra):
    check = {"id": check_id, "skill": "侦查", "difficulty": "normal", "char_ref": hero.name,
             "char_id": hero.id, "actor_name": hero.name, **extra}
    session_service.add_pending_check(db, session.id, check)
    session_service.add_event(db, session.id, "system", "请投侦查", actor_name="系统",
                              metadata={"check_request": True, **check})


def test_cancel_single_pending_check_marks_card(db_factory):
    db = db_factory()
    module, hero, _mate, session = _seed(db)
    _hang_check(db, session, hero, "ck1")
    chunks, note = _run(db, session, module, "cancel_check", {"check_id": "ck1"})
    assert "撤销" in note
    assert session_service.get_pending_check(db, session.id, "ck1") is None
    patch = next(c.as_wire() for c in chunks if c.as_wire()["type"] == "event_patch")
    assert patch["metadata"]["patch"] == {"cancelled": True}
    with pytest.raises(ValueError, match="已经投过或已撤销"):
        _run(db, session, module, "cancel_check", {"check_id": "ck1"})


def test_cancel_last_member_of_group_check_sends_kp_roll_ready(db_factory):
    """群检里有人不投：KP 撤掉他，整批收束，kp_roll_ready 带上已投者的结果。"""
    db = db_factory()
    module, hero, _mate, session = _seed(db)
    _hang_check(db, session, hero, "ck2", check_batch_id="b1",
                check_results=["阿甘 侦查 成功"], check_any_success=True)
    chunks, note = _run(db, session, module, "cancel_check", {"check_id": "ck2"})
    ready = next(c.as_wire() for c in chunks if c.as_wire()["type"] == "kp_roll_ready")
    assert "阿甘 侦查 成功" in ready["metadata"]["description"]
    assert "已被 KP 撤销" in ready["metadata"]["description"]
    assert "整批已收束" in note


def test_proxy_roll_endpoint_starts_roll_chain_with_proxy_flag(db_factory, monkeypatch):
    db = db_factory()
    _module, hero, _mate, session = _seed(db)
    _hang_check(db, session, hero, "ck3")
    started = []

    def _start(sid, coro, **_kw):
        started.append(coro.cr_frame.f_locals.get("proxy"))
        coro.close()

    monkeypatch.setattr(kp_api.generation_manager, "start", _start)
    monkeypatch.setattr(room_hub, "broadcast", lambda *a, **k: None)
    asyncio.run(kp_api.proxy_roll(session.id, "ck3", db, "kp-token"))
    assert started == [True]
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        asyncio.run(kp_api.proxy_roll(session.id, "missing", db, "kp-token"))


# ── 4. 无名 SAN：同轮两次真不同的恐怖都要扣 ──────────────────────────────

def test_two_unnamed_san_checks_in_one_turn_both_settle(db_factory, monkeypatch):
    db = db_factory()
    module, _hero, mate, session = _seed(db, with_ai_mate=True)
    import app.rules.dice as dice
    monkeypatch.setattr(dice.random, "randint", lambda a, b: b)   # 必失败、损失取满
    payload = {"chars": mate.name, "success_loss": "1", "failure_loss": "2"}
    _c, first = _run(db, session, module, "san_check", payload)
    _c, second = _run(db, session, module, "san_check", payload)
    assert "无需重复" not in second
    db.refresh(mate)
    assert mate.system_data["sanity"]["current"] == 56
    # 写了恐怖源的仍按源去重（同一具尸体不该吓两次）
    _run(db, session, module, "san_check", {**payload, "source": "地窖里的尸体"})
    _c, again = _run(db, session, module, "san_check", {**payload, "source": "地窖里的尸体"})
    assert "无需重复" in again


# ── 5. 私有台账 / 改写撤回 / 参谋读笔记 ─────────────────────────────────

def test_ledger_keeps_blind_result_private_and_allows_edit_withdraw(db_factory, monkeypatch):
    db = db_factory()
    _module, _hero, _mate, session = _seed(db)
    broadcasts = []
    monkeypatch.setattr(room_hub, "broadcast", lambda _sid, chunk: broadcasts.append(chunk.as_wire()))
    blind = asyncio.run(kp_action(session.id, KpActionRequest(
        action="generic_roll", payload={"count": 1, "sides": 6, "visibility": "blind"},
    ), db, "kp-token"))
    asyncio.run(kp_action(session.id, KpActionRequest(
        action="narration", payload={"content": "灯灭了。"},
    ), db, "kp-token"))

    db.refresh(session)
    desk = kp_api.get_desk(session.id, db, "kp-token")
    assert desk["ledger"][1]["result"] == blind["result"]           # 暗投点数还在
    narration = desk["ledger"][0]["events"][0]
    assert narration["content"] == "灯灭了。"
    assert blind["result"] not in str(broadcasts)                    # 从没进过广播

    asyncio.run(kp_action(session.id, KpActionRequest(
        action="edit_event", payload={"event_id": narration["id"], "content": "灯忽明忽暗。"},
    ), db, "kp-token"))
    assert db.get(EventLog, narration["id"]).content == "灯忽明忽暗。"
    asyncio.run(kp_action(session.id, KpActionRequest(
        action="withdraw_event", payload={"event_id": narration["id"]},
    ), db, "kp-token"))
    assert db.get(EventLog, narration["id"]) is None
    assert any(b["type"] == "event_delete" for b in broadcasts)
    db.refresh(session)
    ledger = kp_api.get_desk(session.id, db, "kp-token")["ledger"]
    assert next(e for e in ledger if e["action"] == "narration")["events"][0]["withdrawn"]


def test_kp_cannot_edit_player_or_system_events(db_factory):
    db = db_factory()
    module, hero, _mate, session = _seed(db)
    ev = session_service.add_event(db, session.id, "dialogue", "我去开门", actor_id=hero.id,
                                   actor_name=hero.name)
    with pytest.raises(ValueError, match="只能改写或撤回 KP 自己"):
        _run(db, session, module, "edit_event", {"event_id": ev.id, "content": "我不去"})


def test_advisor_context_and_plan_include_kp_notes(db_factory, monkeypatch):
    db = db_factory()
    module, _hero, _mate, session = _seed(db)
    human_kp_service.update_workspace(db, session, notes="守门人其实是邪教徒")
    messages = human_kp_service._advisor_context(db, session.id, session, module, "")
    assert any("守门人其实是邪教徒" in str(m.get("content")) and m["role"] == "system" for m in messages)
    # 插在开头的 system 块里，不落在对话中途
    first_non_system = next(i for i, m in enumerate(messages) if m["role"] != "system")
    assert all(m["role"] == "system" for m in messages[:first_non_system])

    captured = []

    async def _planner(_llm, msgs):
        captured.extend(msgs)
        return None

    monkeypatch.setattr(human_kp_service.turn_planner, "run_turn_planner", _planner)
    monkeypatch.setattr(human_kp_service, "get_fast_llm", lambda: None)
    with pytest.raises(ValueError):
        asyncio.run(human_kp_service.generate_turn_plan(db, session.id, session, module, ""))
    assert any("守门人其实是邪教徒" in str(m.get("content")) for m in captured)


def test_advisor_endpoints_are_rate_limited():
    import inspect

    from app.services.rate_limit import limiter
    for fn in (kp_api.generate_draft, kp_api.generate_plan, kp_api.lookup):
        assert "request" in inspect.signature(fn).parameters
        assert any(k.endswith(fn.__name__) for k in limiter._route_limits)


# ── 6. 流程：检定申请待办 / 全 AI 席自动队友 ─────────────────────────────

def test_player_check_request_lands_in_kp_desk_until_handled(db_factory, monkeypatch):
    from app.schemas.event import CheckRequest

    db = db_factory()
    module, hero, _mate, session = _seed(db)
    monkeypatch.setattr(room_hub, "broadcast", lambda *a, **k: None)
    seat = next(p for p in session_service.get_participants(db, session.id) if p.role == "human")
    seat.owner_token = "player-token"
    db.commit()
    asyncio.run(chat_api.check(session.id, CheckRequest(skill="侦查", intent="翻抽屉"), db, "player-token"))

    db.refresh(session)
    requests = kp_api.get_desk(session.id, db, "kp-token")["check_requests"]
    assert [(r["skill"], r["intent"], r["actor_id"]) for r in requests] == [("侦查", "翻抽屉", hero.id)]

    _run(db, session, module, "dice_check",
         {"skill": "侦查", "char": f"character:{hero.id}", "request_id": requests[0]["id"]})
    db.refresh(session)
    assert kp_api.get_desk(session.id, db, "kp-token")["check_requests"] == []


def test_dismiss_request(db_factory):
    db = db_factory()
    module, hero, _mate, session = _seed(db)
    ev = session_service.add_event(db, session.id, "action", "（申请「聆听」检定）", actor_id=hero.id,
                                   actor_name=hero.name, metadata={"check_request": True, "skill": "聆听"})
    _run(db, session, module, "dismiss_request", {"request_id": ev.id})
    db.refresh(session)
    assert human_kp_desk.desk_payload(db, session.id, session)["check_requests"] == []


def test_all_ai_seats_auto_team_turn_after_narration(db_factory, monkeypatch):
    db = db_factory()
    module = Module(title="独走", rule_system="coc", scenes=[{"id": "s1", "title": "门厅"}], npcs=[])
    a1 = Character(name="阿甘", rule_system="coc", is_player=False)
    a2 = Character(name="温妮", rule_system="coc", is_player=False)
    db.add_all([module, a1, a2])
    db.commit()
    session = session_service.create_session(db, module.id, [
        {"character_id": a1.id, "role": "ai", "is_primary": True},
        {"character_id": a2.id, "role": "ai"},
    ], creator_token="kp-token", kp_mode="human")
    session_service.start_game(db, session.id, "kp-token")
    human_kp_service.update_workspace(db, session, auto_ai_teammates=True)
    started = []

    def _start(sid, coro, **_kw):
        started.append(sid)
        coro.close()

    monkeypatch.setattr(chat_api.generation_manager, "start", _start)
    monkeypatch.setattr(room_hub, "broadcast", lambda *a, **k: None)
    asyncio.run(kp_action(session.id, KpActionRequest(
        action="narration", payload={"content": "雨停了。"},
    ), db, "kp-token"))
    assert started == [session.id]

    # 没开自动 → 不跑
    human_kp_service.update_workspace(db, session, auto_ai_teammates=False)
    asyncio.run(kp_action(session.id, KpActionRequest(
        action="narration", payload={"content": "风起了。"},
    ), db, "kp-token"))
    assert started == [session.id]


# ── 战斗：KP 操控敌人 ───────────────────────────────────────────────────

def test_kp_controls_enemy_turns(db_factory, monkeypatch):
    db = db_factory()
    module, hero, _mate, session = _seed(db)
    _run(db, session, module, "combat_control", {"on": "true"})
    state = _start_combat(db, session, module)
    enemy = next(p for p in state["initiative"] if p["side"] == "enemy")
    # 守门人 DEX 90 先手：驱动停在他身上等 KP，一拳都还没出
    assert state["awaiting_kp"] == enemy["id"]
    assert combat_service._combat_meta(state)["kp_controls_enemies"] is True
    assert hero.system_data["hitPoints"]["current"] == 12

    with pytest.raises(ValueError, match="攻击目标"):
        _run(db, session, module, "combat_npc_act", {"type": "attack", "target_id": enemy["id"]})
    _run(db, session, module, "combat_npc_act", {"type": "attack", "target_id": hero.id})
    db.expire_all()
    state = combat_service.get_combat(db.get(GameSession, session.id))
    # 方格上够不着 → 与启发式同一出口：这一回合走位接近，然后轮到调查员
    moved = next(p for p in state["initiative"] if p["id"] == enemy["id"])
    assert moved["pos"] != enemy["pos"]
    assert combat_service.current_actor(state)["id"] == hero.id
    assert state.get("awaiting_kp") is None
    with pytest.raises(ValueError, match="不是等 KP 下令"):
        _run(db, session, module, "combat_npc_act", {"type": "auto"})


def test_turning_control_off_hands_waiting_enemy_back_to_heuristic(db_factory):
    db = db_factory()
    module, _hero, _mate, session = _seed(db)
    _run(db, session, module, "combat_control", {"on": "true"})
    state = _start_combat(db, session, module)
    assert state["awaiting_kp"]
    _run(db, session, module, "combat_control", {"on": "false"})
    db.expire_all()
    state = combat_service.get_combat(db.get(GameSession, session.id))
    assert state.get("awaiting_kp") is None
    assert combat_service.current_actor(state)["side"] == "player"   # 启发式接手走完敌方回合
    db.refresh(session)
    assert session.kp_state["control_enemies"] is False


def test_enemy_control_off_by_default(db_factory):
    db = db_factory()
    module, _hero, _mate, session = _seed(db)
    state = _start_combat(db, session, module)
    assert not state.get("awaiting_kp")
    assert combat_service.current_actor(state)["side"] == "player"


def test_dice_check_passes_modifier_reason(db_factory):
    db = db_factory()
    module, hero, _mate, session = _seed(db)
    chunks, _ = _run(db, session, module, "dice_check", {
        "skill": "侦查", "char": "npc:guard", "penalty": "1", "modifier_reason": "烛光昏暗",
    })
    notes = str(chunks[0].as_wire()["metadata"]["dice"])
    assert "烛光昏暗" in notes


_ = (SessionParticipant,)
