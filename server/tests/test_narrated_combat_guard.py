"""叙事—机制脱节守卫的单测：KP 把怪物写成已经扑上来，战斗态却没起来时补开战。

覆盖：词表预筛零成本跳过、判定从严不误开战、真交战补开战且幂等、
计划已裁定开战时让位给 _ensure_planned_combat、判定给不出敌方时宁可不开战。
不调真实 LLM。
"""

from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.ai.provider import LLMProvider
from app.ai.turn_planner import CombatPlan, TurnPlan
from app.models.base import Base
from app.models.character import Character
from app.models.event_log import EventLog  # noqa: F401 — 注册建表
from app.models.module import Module
from app.models.session import GameSession
from app.models.session_participant import SessionParticipant  # noqa: F401 — 注册建表
from app.services import combat_service, planned_effects, session_service


@pytest.fixture
def db_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed(db) -> tuple[str, GameSession, Module, Character]:
    """一节车厢、一头按 goals 该扑上来的怪物——即常暗之箱那一轮的最小复刻。"""
    module = Module(
        title="常暗隧道", rule_system="coc",
        scenes=[{"id": "scene_2", "name": "2号车厢"}],
        npcs=[{
            "id": "clicker",
            "name": "循声者",
            "description": "没有眼睛的人形怪物，对声音极其敏感。",
            "goals": ["攻击并杀死一切发出声音的活物。"],
            "initial_location": "scene_2",
            "attributes": {"DEX": 70, "CON": 50, "SIZ": 50},
            "skills": {"格斗(斗殴)": 45, "闪避": 25},
            "weapon": "撕咬",
        }],
    )
    hero = Character(
        name="江户川龙牙", rule_system="coc", is_player=True,
        base_attributes={"DEX": 65, "CON": 55, "SIZ": 60},
        system_data={"hitPoints": {"current": 11, "max": 11}},
        skills={"侦查": 55, "闪避": 50},
    )
    db.add_all([module, hero])
    db.flush()
    session = GameSession(
        module_id=module.id, player_character_id=hero.id, status="active",
        current_scene_id="scene_2", world_state={},
    )
    db.add(session)
    db.commit()
    return session.id, session, module, hero


class _FakeJudgeLLM(LLMProvider):
    """只回放一份预设裁决的快模型；记录调用次数以验证预筛是否真的省掉了调用。"""

    def __init__(self, verdict: dict | str):
        self.verdict = verdict
        self.calls: list[list[dict]] = []

    def supports_tools(self) -> bool:
        return False

    async def complete(self, messages, temperature=0.7, max_tokens=None, response_format=None):
        self.calls.append(messages)
        if isinstance(self.verdict, str):
            return self.verdict
        return json.dumps(self.verdict, ensure_ascii=False)

    async def stream(self, messages, temperature=0.7, max_tokens=None):
        yield ""


def _run_guard(db, session_id, session, module, hero, plan, pre_gen_seq, judge, monkeypatch):
    monkeypatch.setattr("app.ai.llm_factory.get_fast_llm", lambda: judge)
    return asyncio.run(_collect(
        planned_effects._ensure_narrated_combat(
            db, session_id, session, module, hero, [], None, plan, pre_gen_seq,
        )
    ))


async def _collect(agen):
    return [chunk async for chunk in agen]


def _narrate(db, session_id, text: str) -> None:
    session_service.add_event(db, session_id, "narration", text, actor_name="KP")


# ── 预筛：不含实际攻击动作的叙事一律零成本跳过 ────────────────────────────


def test_no_attack_marker_skips_without_llm_call(db_factory, monkeypatch):
    """只写了动静、痕迹、逼近的叙事：连快模型都不该调，更不能开战。"""
    db = db_factory()
    session_id, session, module, hero = _seed(db)
    pre_gen_seq = session_service.get_next_sequence_num(db, session_id) - 1
    _narrate(db, session_id, "黑暗里那阵粗重的吐息停了。座椅深处没有动静，门缝里也没有。")
    judge = _FakeJudgeLLM({"engaged": True, "enemies": ["循声者"], "trigger": "不该走到这一步"})

    chunks = _run_guard(
        db, session_id, session, module, hero, TurnPlan(), pre_gen_seq, judge, monkeypatch,
    )

    assert chunks == []
    assert judge.calls == []          # 预筛挡住，没花钱
    assert not combat_service.get_combat(db.get(GameSession, session_id))


# ── 判定从严：命中词表但并未真打起来 ──────────────────────────────────────


def test_marker_hit_but_judge_says_not_engaged(db_factory, monkeypatch):
    """怪物只是循声摸索、还没扑到人：判定说 false，就不许开战。"""
    db = db_factory()
    session_id, session, module, hero = _seed(db)
    pre_gen_seq = session_service.get_next_sequence_num(db, session_id) - 1
    _narrate(db, session_id, "它猛地起身，抓向声音传来的那片空处，爪尖擦着椅背划过。")
    judge = _FakeJudgeLLM({"engaged": False, "enemies": [], "trigger": ""})

    chunks = _run_guard(
        db, session_id, session, module, hero, TurnPlan(), pre_gen_seq, judge, monkeypatch,
    )

    assert chunks == []
    assert len(judge.calls) == 1      # 预筛命中「抓向」，确实问了一次
    assert not combat_service.get_combat(db.get(GameSession, session_id))


# ── 真交战：补开战，且幂等 ────────────────────────────────────────────────


def test_narrated_charge_starts_combat_and_is_idempotent(db_factory, monkeypatch):
    """叙事写明怪物已经扑上来 → 补开战；再跑一次不重复创建。"""
    db = db_factory()
    session_id, session, module, hero = _seed(db)
    pre_gen_seq = session_service.get_next_sequence_num(db, session_id) - 1
    _narrate(
        db, session_id,
        "那只蜷在门前的黑影，连试探都没有，直接从趴伏的姿势弹射而起，朝声源的方向扑来。",
    )
    judge = _FakeJudgeLLM({
        "engaged": True, "target": "江户川龙牙", "enemies": ["循声者"],
        "trigger": "巨响引来了循声者",
    })

    first = _run_guard(
        db, session_id, session, module, hero, TurnPlan(), pre_gen_seq, judge, monkeypatch,
    )

    state = combat_service.get_combat(db.get(GameSession, session_id))
    assert state and state["active"] is True
    # 参战方的 name 是**对外称呼**（玩家还没认出这东西时会被遮），
    # 这里测的是「战斗有没有起来」，按真名断言
    assert any(p.get("true_name") == "循声者" for p in state["initiative"])
    assert any(chunk.type == "combat_start" for chunk in first)

    second = _run_guard(
        db, session_id, session, module, hero, TurnPlan(), pre_gen_seq, judge, monkeypatch,
    )
    assert second == []
    assert len(judge.calls) == 1      # 已在战斗态，直接返回，不再问第二次


# ── 分工：计划已裁定开战时让位，别开两次 ──────────────────────────────────


def test_yields_to_planned_combat_guard(db_factory, monkeypatch):
    """plan 已判 should_start：交给 _ensure_planned_combat，本守卫连判定都不跑。"""
    db = db_factory()
    session_id, session, module, hero = _seed(db)
    pre_gen_seq = session_service.get_next_sequence_num(db, session_id) - 1
    _narrate(db, session_id, "黑影扑来，利爪挥向他的面门。")
    plan = TurnPlan(combat=CombatPlan(should_start=True, enemies=["循声者"], trigger="巨响引怪"))
    judge = _FakeJudgeLLM({"engaged": True, "enemies": ["循声者"], "trigger": "不该由我来开"})

    chunks = _run_guard(
        db, session_id, session, module, hero, plan, pre_gen_seq, judge, monkeypatch,
    )

    assert chunks == []
    assert judge.calls == []
    assert not combat_service.get_combat(db.get(GameSession, session_id))


# ── 挨打的必须是玩家这一方 ────────────────────────────────────────────────


def test_npc_brawl_does_not_drag_party_into_combat(db_factory, monkeypatch):
    """两个 NPC 扭打成一团、玩家在旁边看着：打不到玩家头上就不该起战斗轮。

    实测判定会在这种场面上翻车（判成开战，还把叙事里没出场的怪物拉进来凑敌人），
    故由 target 做确定性护栏——这条测试锁的就是那次翻车。
    """
    db = db_factory()
    session_id, session, module, hero = _seed(db)
    pre_gen_seq = session_service.get_next_sequence_num(db, session_id) - 1
    _narrate(
        db, session_id,
        "京山人吉忽然扑向那个陌生男人，两人撞翻在过道上扭作一团，拳头砸向对方的肋侧。"
        "龙牙站在两米外，一时没能插进去。",
    )
    judge = _FakeJudgeLLM({
        "engaged": True, "target": "陌生男人", "enemies": ["循声者"],
        "trigger": "循声者也被声音引来了",
    })

    chunks = _run_guard(
        db, session_id, session, module, hero, TurnPlan(), pre_gen_seq, judge, monkeypatch,
    )

    assert chunks == []
    assert not combat_service.get_combat(db.get(GameSession, session_id))


def test_target_short_name_still_counts_as_party(db_factory, monkeypatch):
    """叙事里用简称「龙牙」称呼「江户川龙牙」：护栏不能因此把真交战判掉。"""
    db = db_factory()
    session_id, session, module, hero = _seed(db)
    pre_gen_seq = session_service.get_next_sequence_num(db, session_id) - 1
    _narrate(db, session_id, "那东西扑来，一口咬住龙牙的小臂。")
    judge = _FakeJudgeLLM({
        "engaged": True, "target": "龙牙", "enemies": ["循声者"], "trigger": "它咬住了龙牙",
    })

    chunks = _run_guard(
        db, session_id, session, module, hero, TurnPlan(), pre_gen_seq, judge, monkeypatch,
    )

    state = combat_service.get_combat(db.get(GameSession, session_id))
    assert state and state["active"] is True
    assert any(chunk.type == "combat_start" for chunk in chunks)


# ── 宁可不开战：判定给不出敌方 ────────────────────────────────────────────


def test_no_enemy_name_refuses_to_start(db_factory, monkeypatch):
    """判定说打起来了却只报出玩家自己的名字：不凭空造敌，维持现状。"""
    db = db_factory()
    session_id, session, module, hero = _seed(db)
    pre_gen_seq = session_service.get_next_sequence_num(db, session_id) - 1
    _narrate(db, session_id, "他一拳挥出，砸向黑暗里的什么东西。")
    judge = _FakeJudgeLLM({
        "engaged": True, "target": "江户川龙牙", "enemies": ["江户川龙牙"],
        "trigger": "玩家动手",
    })

    chunks = _run_guard(
        db, session_id, session, module, hero, TurnPlan(), pre_gen_seq, judge, monkeypatch,
    )

    assert chunks == []
    assert not combat_service.get_combat(db.get(GameSession, session_id))


def test_unparsable_verdict_refuses_to_start(db_factory, monkeypatch):
    """快模型返回的不是 JSON（推理模型把预算耗空等）：守卫失败就是不开战。"""
    db = db_factory()
    session_id, session, module, hero = _seed(db)
    pre_gen_seq = session_service.get_next_sequence_num(db, session_id) - 1
    _narrate(db, session_id, "黑影扑来，利爪挥向他的面门。")
    judge = _FakeJudgeLLM("")

    chunks = _run_guard(
        db, session_id, session, module, hero, TurnPlan(), pre_gen_seq, judge, monkeypatch,
    )

    assert chunks == []
    assert not combat_service.get_combat(db.get(GameSession, session_id))
