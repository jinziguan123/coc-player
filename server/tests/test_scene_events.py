"""剧情事件（kind=event）不是地点 + 事件闸门：用「陵墓」模组的真实数据结构做夹具。

两个线上问题的回归：
1. 解析把「导入：困于帝王谷」「可怖之物苏醒」解析成 kind=event，下游只排除 chapter，
   于是事件上了地图、可前往；
2. 幕后推演看不到事件条件，玩家还在甬道时就按「吃掉闯入者」的目标把可怖之物放出来。
"""

from __future__ import annotations

import asyncio
import copy
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.ai import turn_planner
from app.ai.agents import backstage_agent
from app.ai.context import build_kp_context
from app.models.base import Base
from app.models.character import Character
from app.models.module import Module
from app.models.session import GameSession
from app.models.session_participant import SessionParticipant  # noqa: F401 — 注册建表
from app.services import (
    chat_service,
    hex_map,
    module_service,
    planned_effects,
    scene_kind,
    session_service,
    turn_context,
)

# ── 陵墓夹具（字段与本地库里该模组一致；事件按新解析口径带 when/npcs/set_flags）──

TOMB_SCENES = [
    {"id": "scene_1", "title": "甬道", "kind": "location", "description": "狭长的甬道。",
     "connections": ["scene_2"], "map": {"q": -3, "r": 2, "biome": "interior"},
     "events": [], "states": []},
    {"id": "scene_2", "title": "前室", "kind": "location", "description": "堆满陪葬品的前室。",
     "connections": ["scene_1", "scene_3", "scene_4"],
     "map": {"q": -1, "r": -1, "biome": "interior"}, "events": [], "states": []},
    {"id": "scene_3", "title": "耳室", "kind": "location", "description": "卡纳的遗骸。",
     "connections": ["scene_2"], "map": {"q": 1, "r": -3, "biome": "interior"},
     "events": [], "states": []},
    {"id": "scene_4", "title": "墓室", "kind": "location", "description": "巨大的石棺。",
     "connections": ["scene_2", "scene_5"], "map": {"q": 2, "r": 0, "biome": "interior"},
     "events": [{"trigger": "仔细查看木质外棺上的祭祀场景", "kind": "san_check",
                 "san_loss": "1/1D3"}],
     "states": [{"when": ["monster_awake"], "danger": "deadly"}]},
    {"id": "scene_5", "title": "藏宝室", "kind": "location", "description": "卡诺匹斯罐。",
     "connections": ["scene_4"], "map": {"q": 0, "r": 3, "biome": "interior"},
     "events": [], "states": []},
    {"id": "scene_0", "title": "导入：困于帝王谷", "kind": "event",
     "description": "工人们吊起石板，调查员冲入墓室后，身后的石板轰然坠落。",
     "when": "开场", "npcs": [], "set_flags": ["trapped_in_tomb"],
     "connections": ["scene_1"], "map": {"q": 0, "r": 0, "biome": "interior"},
     "events": [], "states": []},
    {"id": "scene_6", "title": "可怖之物苏醒", "kind": "event",
     "description": "约30-40分钟后（调查员探索完前室和耳室时），可怖之物移开石棺盖、"
                    "以惊人膂力推开沉重石门，从墓室走出。",
     "when": "约30-40分钟后（调查员探索完前室和耳室时）",
     "npcs": ["npc_1"], "set_flags": ["monster_awake"],
     "connections": ["scene_2", "scene_4"], "map": {"q": -1, "r": 2, "biome": "interior"},
     "events": [], "states": []},
]

TOMB_NPCS = [
    {"id": "npc_1", "name": "可怖之物", "aliases": ["怪物", "木乃伊"],
     "description": "包裹在腐烂亚麻布中的金狼头巨怪。", "personality": "饥饿、狂怒",
     "secrets": ["其心脏被锁在藏宝室的卡诺匹斯罐中。"], "initial_location": "scene_4",
     "goals": ["吃掉所有闯入陵墓的人", "挣脱石板封堵的入口，逃入沙漠"],
     "states": [{"when": ["monster_awake"], "initial_location": "scene_1",
                 "personality": "活跃并疯狂狩猎"},
                {"when": ["heart_destroyed"], "alive": False}]},
    {"id": "npc_2", "name": "萨沙·卡纳", "aliases": ["卡纳"], "description": "骷髅。",
     "secrets": ["日记中可能记录了关于斯芬克斯之子的警告。"],
     "initial_location": "scene_3", "goals": []},
]

TOMB_TRIGGERS = [
    {"id": "trig_2", "when": "经过约30-40分钟游戏时间，或调查员已探索完前室和耳室",
     "set_flags": ["monster_awake"], "clear_flags": ["trapped_in_tomb"],
     "description": "可怖之物从石棺中苏醒，移开石门进入前室。"},
]

TOMB_MAP_NODES = [
    {"id": sid, "q": s["map"]["q"], "r": s["map"]["r"], "biome": "interior", "scene_id": sid}
    for s in TOMB_SCENES for sid in [s["id"]]
]

EVENT_IDS = {"scene_0", "scene_6"}
LOCATION_IDS = {"scene_1", "scene_2", "scene_3", "scene_4", "scene_5"}


def _tomb_module(**kw) -> Module:
    return Module(
        title="陵墓", rule_system="coc", description="", world_setting={},
        scenes=copy.deepcopy(TOMB_SCENES), npcs=copy.deepcopy(TOMB_NPCS), clues=[],
        triggers=copy.deepcopy(TOMB_TRIGGERS), handouts=[],
        map_nodes=copy.deepcopy(TOMB_MAP_NODES), truth="调查员的发掘唤醒了可怖之物。", **kw,
    )


def _in_corridor(flags: list[str] | None = None) -> GameSession:
    return GameSession(
        module_id="m", status="active", current_scene_id="scene_1",
        world_state={"flags": list(flags or [])},
    )


def _pc() -> Character:
    return Character(name="调查员甲", rule_system="coc", is_player=True,
                     base_attributes={}, skills={}, system_data={})


# ── 1. 事件不是地点 ──────────────────────────────────────────────


def test_场景类型判定_缺省与未知值都算地点():
    assert scene_kind.is_location({"id": "a"})
    assert scene_kind.is_location({"id": "a", "kind": "Location"})
    assert scene_kind.is_location({"id": "a", "kind": "landmark"})   # 未知值 → location
    assert not scene_kind.is_location({"id": "a", "kind": "event"})
    assert not scene_kind.is_location({"id": "a", "kind": "chapter"})


def test_入库规整_未知kind归一_事件补齐字段():
    scenes = [
        {"id": "s1", "title": "大厅", "kind": "room"},
        {"id": "s2", "title": "怪物苏醒", "kind": "EVENT", "npcs": ["npc_1", "npc_ghost"],
         "keywords": ["怪物"], "map": {"q": 0, "r": 0}},
    ]
    out = module_service._normalize_scenes(scenes, [{"id": "npc_1"}])
    by_id = {s["id"]: s for s in out}
    assert by_id["s1"]["kind"] == "location"
    ev = by_id["s2"]
    assert ev["kind"] == "event"
    assert ev["npcs"] == ["npc_1"]                # 幻觉 NPC id 被丢弃
    assert ev["set_flags"] == ["s2_triggered"]    # 没写标志 → 确定性兜底
    assert ev["when"] == ""
    assert ev["keywords"] == []                   # 事件不是地点，没有解锁词
    assert "map" not in ev                         # 不上沙盘


def test_事件不进地图节点():
    module = _tomb_module()
    scenes = module_service._normalize_scenes(module.scenes, module.npcs)
    nodes = module_service._normalize_map_nodes(module.map_nodes, scenes)
    scene_nodes = {n["scene_id"] for n in nodes if n.get("scene_id")}
    assert scene_nodes == LOCATION_IDS


def test_事件不出现在地图与可前往列表():
    module = _tomb_module()
    session = _in_corridor()
    # 极端情况：KP 建议过前往事件、旁白也提到了事件标题——仍不能上图
    session.world_state["travel_suggested"] = ["scene_6"]
    narration = type("E", (), {
        "event_type": "narration", "visibility": ["all"],
        "content": "导入：困于帝王谷之后，可怖之物苏醒的传说在你们脑中回响。前室就在前方。",
    })()
    known = session_service.known_scene_ids(module, session, [narration])
    assert not (known & EVENT_IDS)
    shown = session_service.list_known_locations(module, session, events=[narration])
    assert not ({x["id"] for x in shown} & EVENT_IDS)
    god = session_service.list_known_locations(module, session, reveal_all=True)
    assert {x["id"] for x in god} == LOCATION_IDS


def test_事件不可作为场景切换与前往目标():
    module = _tomb_module()
    assert turn_context._resolve_scene_ref(module, "可怖之物苏醒") is None
    assert turn_context._resolve_scene_ref(module, "scene_6") is None
    assert turn_context._resolve_scene_ref(module, "前室") == "scene_2"
    # 事件的 connections 不构成通道：甬道的邻居只有前室
    assert session_service.scene_neighbors(module, "scene_1") == ["scene_2"]
    assert "scene_6" not in session_service.scene_neighbors(module, "scene_2")


def test_沙盘拖拽拒绝事件():
    module = _tomb_module()
    with pytest.raises(ValueError):
        hex_map.set_scene_map(None, module, "scene_6", 5, 5)


def test_地点父级指向事件时按顶层处理():
    scenes = [
        {"id": "hall", "title": "走廊", "kind": "location",
         "map": {"q": 0, "r": 0, "biome": "interior", "parent": "ev"}},
        {"id": "ev", "title": "走廊中的恐怖事件", "kind": "event"},
    ]
    hex_map.ensure_scene_maps(scenes)
    assert hex_map.scene_parent(scenes[0]) == ""


# ── 2. 事件闸门：幕后推演与 KP 不抢跑 ─────────────────────────────


def test_蛰伏判定随事件标志切换():
    assert set(scene_kind.dormant_npcs(TOMB_SCENES, set())) == {"npc_1"}
    assert scene_kind.dormant_npcs(TOMB_SCENES, {"monster_awake"}) == {}


def test_玩家在甬道时幕后推演看不到可怖之物为可行动主体():
    module = _tomb_module()
    session = _in_corridor()
    secret = backstage_agent.npcs_with_secrets(module)
    active, dormant = backstage_agent.split_dormant(module, secret, set())
    assert [n["id"] for n in active] == ["npc_2"]
    assert [n["id"] for n in dormant] == ["npc_1"]

    # 即便调用方把全部主体都传进来，消息构建也会再滤一次
    messages = backstage_agent.build_backstage_messages(session, module, secret, [])
    payload = json.loads(messages[1]["content"])
    assert [n["id"] for n in payload["npcs"]] == ["npc_2"]
    assert payload["dormant_npcs"] == [{"name": "可怖之物", "waiting_for": ["可怖之物苏醒"]}]
    assert [e["title"] for e in payload["pending_events"]] == [
        "导入：困于帝王谷", "可怖之物苏醒",
    ]
    assert payload["pending_events"][1]["when"].startswith("约30-40分钟后")
    assert {s["id"] for s in payload["scenes"]} == LOCATION_IDS
    assert "visited_scenes" in payload
    assert "事件条件未满足前" in messages[0]["content"]


@pytest.fixture
def db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


class _StubLLM:
    def __init__(self, payload: str):
        self.payload = payload
        self.calls = 0
        self.messages: list = []

    async def complete(self, messages, **kw):
        self.calls += 1
        self.messages.append(messages)
        return self.payload


_APPROACH = json.dumps({"events": [{
    "npc_id": "npc_1", "action": "破棺而出，沿甬道逼近调查员",
    "affected_scene": "scene_1", "suggest_flags": ["monster_awake"],
}]}, ensure_ascii=False)


def _seed(db, flags: list[str]):
    module = _tomb_module()
    player = Character(name="调查员甲", rule_system="coc")
    db.add_all([module, player])
    db.flush()
    session = GameSession(
        module_id=module.id, player_character_id=player.id, current_scene_id="scene_1",
        status="active", world_state={"flags": flags},
    )
    db.add(session)
    db.commit()
    for i in range(8):
        session_service.add_event(
            db, session.id, "action", f"我在甬道里摸索第{i}块砖",
            actor_id=player.id, actor_name=player.name,
        )
    return session


def _backstage_events(db, session):
    return [
        e for e in session_service.get_session_events(db, session.id)
        if (e.metadata_ or {}).get("kind") == "backstage"
    ]


def test_玩家在甬道时幕后推演不产出可怖之物逼近的事件(db):
    session = _seed(db, flags=[])
    llm = _StubLLM(_APPROACH)          # 模型无视规则硬编出怪物逼近
    asyncio.run(chat_service._maybe_run_backstage(db, session.id, llm))
    assert llm.calls == 1              # 卡纳仍可推演
    payload = json.loads(llm.messages[0][1]["content"])
    assert "npc_1" not in {n["id"] for n in payload["npcs"]}
    assert _backstage_events(db, session) == []    # 蛰伏者的动作在解析时被丢弃


def test_事件触发后可怖之物重新进入推演(db):
    session = _seed(db, flags=["monster_awake"])
    llm = _StubLLM(_APPROACH)
    asyncio.run(chat_service._maybe_run_backstage(db, session.id, llm))
    evs = _backstage_events(db, session)
    assert len(evs) == 1 and evs[0].metadata_["npc_id"] == "npc_1"


def test_全员蛰伏时幕后推演零调用(db):
    session = _seed(db, flags=[])
    module = db.get(Module, session.module_id)
    module.npcs = [n for n in copy.deepcopy(TOMB_NPCS) if n["id"] == "npc_1"]
    db.commit()
    llm = _StubLLM(_APPROACH)
    asyncio.run(chat_service._maybe_run_backstage(db, session.id, llm))
    assert llm.calls == 0


def test_KP上下文给出待触发事件与蛰伏标注():
    module = _tomb_module()
    session = _in_corridor()
    session.world_state["visited_scenes"] = ["scene_1", "scene_2", "scene_4"]
    session.current_scene_id = "scene_4"
    ev = type("E", (), {
        "event_type": "narration", "visibility": ["all"], "content": "石棺静静横陈。",
        "actor_name": "KP", "actor_id": None, "metadata_": {}, "sequence_num": 1,
    })()
    text = "\n".join(str(m.get("content") or "") for m in build_kp_context(
        session, module, _pc(), [ev],
    ))
    assert "【剧情事件】" in text
    assert "[待触发]「可怖之物苏醒」" in text
    assert "[SET_FLAG: monster_awake]" in text
    assert "蛰伏中：待剧情事件「可怖之物苏醒」触发前" in text

    session.world_state["flags"] = ["monster_awake", "trapped_in_tomb"]
    text = "\n".join(str(m.get("content") or "") for m in build_kp_context(
        session, module, _pc(), [ev],
    ))
    assert "[已触发]「可怖之物苏醒」" in text
    assert "蛰伏中" not in text


def test_规划器看得到蛰伏与待触发事件_开战被确定性拦截():
    module = _tomb_module()
    session = _in_corridor()
    session.current_scene_id = "scene_4"
    messages = turn_planner.build_turn_plan_messages(session, module, _pc(), [])
    content = messages[-1]["content"]
    payload, _ = json.JSONDecoder().raw_decode(content, content.index("{"))
    assert {e["title"] for e in payload["pending_events"]} == {"导入：困于帝王谷", "可怖之物苏醒"}
    assert not ({s["id"] for s in payload["canonical_scene_facts"]} & EVENT_IDS)
    monster = next(n for n in payload["visible_npcs"] if n["id"] == "npc_1")
    assert monster["status"].startswith("蛰伏")
    # 导演信号里的未触发 trigger 带上了条件，不再是「随时可推」的悬念
    assert "（未触发，条件：经过约30-40分钟游戏时间" in content

    assert planned_effects._dormant_npc_names(module, session) == {"可怖之物", "怪物", "木乃伊"}
    session.world_state["flags"] = ["monster_awake"]
    assert planned_effects._dormant_npc_names(module, session) == set()


def test_开局落在第一个地点而非排在前面的事件(db):
    module = _tomb_module()
    module.scenes = [s for s in module.scenes if s["kind"] == "event"] + [
        s for s in module.scenes if s["kind"] != "event"
    ]
    player = Character(name="调查员甲", rule_system="coc", is_player=True)
    db.add_all([module, player])
    db.commit()
    session = session_service.create_session(
        db, module.id, [{"character_id": player.id, "is_primary": True}],
    )
    assert session.current_scene_id == "scene_1"
