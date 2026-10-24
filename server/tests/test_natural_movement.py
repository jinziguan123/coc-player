"""自然语言移动、个人离队与空间切换（以《陵墓》为例）。

复现的真实失败（本地存档 27af5ba9，2026-10-22）：阿尔文说「直接从这个缺口钻进去」，KP 叙述他钻了
进去，但没调 scene_change；规划器其实已解析出 scene_2（前室），却被旧的移动证据
``_explicit_player_movement`` 以「原文没说『前室』」为由拒绝（app.log：「规划器场景切换缺少玩家
移动证据，已跳过 target=scene_2」）——全队的位置从头到尾停在甬道。

这里的用例走真实编排：规划器（替身返回固定计划）→ 叙事前移动裁定 → 分头分组 → KP 工具循环
（原生工具 / 文本指令适配层）→ 后置守卫 → 持久化，而不只测解析函数。
"""

from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.ai import turn_planner
from app.ai.provider import LLMProvider, StreamDelta, ToolCall
from app.ai.turn_planner import MovePlan, ScenePolicy, TurnPlan
from app.models import Base, Character, GameSession, Module  # noqa: F401
from app.services import (
    chat_service,
    check_gating,
    kp_tool_loop,
    movement,
    session_service,
    turn_orchestrator,
)
from tests.text_commands import run_text_commands


def test_无模组时参与者快照不解析去向(monkeypatch):
    monkeypatch.setattr(session_service, "get_char_location", lambda *_: "hall")
    monkeypatch.setattr(session_service, "is_human_controlled", lambda *_: True)

    def unexpected_resolution(*_):
        pytest.fail("缺少模组时不应解析去向")

    monkeypatch.setattr(movement, "resolve_destination", unexpected_resolution)
    member = movement._participant(
        None, "session", SimpleNamespace(), SimpleNamespace(id="player"),
        movement.Statement(kind="act"), module=None,
        resolve_from="hall", resolve_target="room",
    )
    assert member.destination is None
    assert member.location == "hall"
    assert member.human is True


# 《陵墓》的真实场景数据（描述、关键词、连通照抄本地模组，省略与移动无关的字段）。
TOMB_SCENES = [
    {
        "id": "scene_1", "title": "甬道", "kind": "location", "connections": ["scene_2"],
        "keywords": ["入口", "甬道", "走廊"],
        "description": "长约25英尺的走廊，墙上有磨损的象形文字和壁画。尽头有一扇封死的拱门，"
                       "左下角有一个被凿开的石膏墙洞。",
    },
    {
        "id": "scene_2", "title": "前室", "kind": "location",
        "connections": ["scene_1", "scene_3", "scene_4"], "keywords": ["前室", "宝物室"],
        "description": "堆满宝物的房间，西北角有金制战车和划艇。东侧有巨大石板挡住通往墓室的拱门，"
                       "两侧有兽首木乃伊守卫。北侧拱门通往耳室。",
    },
    {
        "id": "scene_3", "title": "耳室", "kind": "location", "connections": ["scene_2"],
        "keywords": ["神龛", "耳室"],
        "description": "摆放着宗教器物的房间，墙上有丧葬锥和两幅大型壁画。",
    },
    {
        "id": "scene_4", "title": "墓室", "kind": "location", "connections": ["scene_2", "scene_5"],
        "keywords": ["墓室", "棺材"],
        "description": "巨大的棺材占据了房间，七层木质外棺已裂开，露出金质内棺。南面有拱门通往藏宝室。",
    },
    {
        "id": "scene_5", "title": "藏宝室", "kind": "location", "connections": ["scene_4"],
        "keywords": ["石箱", "藏宝室"],
        "description": "满是珍奇之物的房间，藏有一个石箱，里面有五个卡诺匹斯罐。",
    },
]


@pytest.fixture
def db_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'move.db'}", connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


class Tomb(SimpleNamespace):
    def loc(self, char) -> str | None:
        self.db.expire_all()
        return session_service.get_char_location(self.db.get(GameSession, self.sid), char.id)

    @property
    def session(self) -> GameSession:
        return self.db.get(GameSession, self.sid)

    def say(self, char, text, etype="action", **meta):
        return session_service.add_event(
            self.db, self.sid, etype, text, actor_id=char.id, actor_name=char.name,
            metadata=meta or None,
        )

    def others(self):
        return [c for c in self.party if c.id != self.alvin.id]

    def move_events(self):
        return [
            e for e in session_service.get_session_events(self.db, self.sid)
            if (e.metadata_ or {}).get("kind") == "move"
        ]


def _seed(db, *, human_hassan=False, start="scene_1", npcs=None, scenes=None) -> Tomb:
    module = Module(
        title="陵墓", rule_system="coc", npcs=npcs or [],
        scenes=copy.deepcopy(scenes or TOMB_SCENES),
    )
    alvin = Character(name="阿尔文", rule_system="coc", is_player=True, skills={"侦查": 31, "攀爬": 50})
    nadia = Character(name="纳迪娅", rule_system="coc", skills={"攀爬": 99})
    karim = Character(name="卡里姆", rule_system="coc", skills={"攀爬": 1})
    hassan = Character(name="哈桑", rule_system="coc", is_player=True, skills={"攀爬": 40})
    db.add_all([module, alvin, nadia, karim, hassan])
    db.commit()
    seats = [
        {"character_id": alvin.id, "is_primary": True, "role": "human"},
        {"character_id": nadia.id, "role": "ai"},
        {"character_id": karim.id, "role": "ai"},
        {"character_id": hassan.id, "role": "human" if human_hassan else "ai"},
    ]
    gs = session_service.create_session(db, module.id, seats)
    gs.status = "active"
    gs.current_scene_id = start
    db.commit()
    session_service.update_scene(db, gs.id, start)
    t = Tomb(db=db, sid=gs.id, module=module, alvin=alvin, nadia=nadia, karim=karim, hassan=hassan)
    t.party = [alvin, nadia, karim, hassan]
    # 上一段 KP 旁白：本回合从它之后开始。
    session_service.add_event(db, gs.id, "narration", "甬道尽头是一扇封死的拱门，左下角有个新凿的洞。")
    return t


def _plan(*moves: MovePlan, scene_change: str | None = None, **kw) -> TurnPlan:
    return TurnPlan(scene_policy=ScenePolicy(moves=list(moves), scene_change=scene_change), **kw)


# ── 生成链路替身 ──────────────────────────────────────────────────────


class _Profile:
    def __init__(self, use_tool_calls: bool):
        self.use_tool_calls = use_tool_calls


class _KP(LLMProvider):
    """原生工具 KP：每次请求按剧本吐一段；``text_script`` 给纯文本路径（经文本指令适配层）。"""

    def __init__(self, scripts=None, text_script: str = "我在原处。"):
        self.scripts = scripts or [[StreamDelta(kind="text", text="（叙事）")]]
        self.text_script = text_script
        self.calls: list[list[dict]] = []

    def supports_tools(self) -> bool:
        return True

    async def complete(self, messages, temperature=0.7, max_tokens=None, response_format=None, timeout=None):
        return ""

    async def stream(self, messages, temperature=0.7, max_tokens=None):
        self.calls.append([dict(m) for m in messages])
        yield self.text_script

    async def stream_chat(self, messages, tools=None, temperature=0.7, max_tokens=None):
        self.calls.append([dict(m) for m in messages])
        script = self.scripts[min(len(self.calls) - 1, len(self.scripts) - 1)]
        for delta in script:
            yield delta


def _runtime(monkeypatch, db_factory, kp: _KP, *, native=True, plan_for_regen=None):
    import app.database as database
    from app.ai import turn_validator
    from app.ai import profile_store
    from app.services.room_hub import room_hub

    monkeypatch.setattr(database, "SessionLocal", db_factory)
    monkeypatch.setattr(chat_service, "get_llm", lambda: kp)
    monkeypatch.setattr(chat_service, "get_fast_llm", lambda: kp)
    monkeypatch.setattr(room_hub, "broadcast", lambda *a, **k: None)
    monkeypatch.setattr(profile_store, "load_active_profile", lambda: _Profile(native))

    async def no_violation(*a, **k):
        return turn_validator.TurnValidation(violated=False)

    monkeypatch.setattr(turn_validator, "validate_turn_narration", no_violation)
    if plan_for_regen is not None:
        async def planner(llm, messages):
            return plan_for_regen.model_copy(deep=True)
        monkeypatch.setattr(turn_planner, "run_turn_planner", planner)


def _generate(t: Tomb, plan: TurnPlan | None):
    events = session_service.get_session_events(t.db, t.sid)
    asyncio.run(chat_service._run_generation(
        t.db, t.sid, t.session, t.module, t.alvin, events, teammates=t.others(), plan=plan,
    ))


def _group_of(messages: list[dict]) -> str:
    """分头生成时这次请求聚焦的场景（看末尾的聚焦提示里点的是哪一处）。"""
    focus = str(messages[-1].get("content") or "")
    return "前室" if "「前室」" in focus else "甬道" if "「甬道」" in focus else ""


def _system_text(messages: list[dict]) -> str:
    return "\n".join(str(m.get("content") or "") for m in messages if m.get("role") == "system")


# ── 陵墓：独自穿过拱门 ───────────────────────────────────────────────


def test_陵墓_独自穿过拱门_只有本人移动_队友原地_后续按位置分头(db_factory, monkeypatch):
    kp = _KP()   # KP 只写叙事、不调 scene_change（真实存档里就是这样）
    _runtime(monkeypatch, db_factory, kp)
    t = _seed(db_factory())
    t.say(t.alvin, "我抛下队友，从拱门钻进去")
    plan = _plan(MovePlan(actors=["阿尔文"], exit="拱门", target="scene_2", solo=True))

    _generate(t, plan)

    assert t.loc(t.alvin) == "scene_2"
    assert t.session.current_scene_id == "scene_2"          # 大地图锚点跟着房主
    for mate in t.others():
        assert t.loc(mate) == "scene_1"                     # 队友没被回落「带走」
    # 留守者的位置被写实（不再依赖 current_scene_id 回落）
    explicit = session_service.get_party_locations(t.session)
    assert explicit[t.nadia.id] == "scene_1" and explicit[t.karim.id] == "scene_1"
    assert len(t.move_events()) == 1
    # 叙事前已落位 → KP 按两处分头生成：每组各一次，且每组都拿到同一份移动裁定。
    groups = chat_service._location_groups(t.session, t.module, t.alvin, t.others())
    assert {g["scene_id"] for g in groups} == {"scene_1", "scene_2"}
    assert len(kp.calls) == 2
    by_group = {_group_of(m): _system_text(m) for m in kp.calls}
    assert set(by_group) == {"前室", "甬道"}
    for text in by_group.values():
        assert "本轮移动裁定" in text and "阿尔文" in text


def test_同一输入_规划器只给旧字段且KP漏调_统一兜底只执行一次(db_factory, monkeypatch):
    """真实存档原话：规划器给出 scene_2，KP 没调工具；队友当场劝阻（「别急着钻进去」）。"""
    kp = _KP()
    plan = _plan(scene_change="scene_2")
    _runtime(monkeypatch, db_factory, kp, plan_for_regen=plan)
    t = _seed(db_factory())
    t.say(t.alvin, "我不想和这三个书呆子多说，直接从这个缺口钻进去")
    t.say(t.nadia, "等一下，大夫——别急着一头钻进去，先让灯照到另一侧再抬脚。", "dialogue")
    t.say(t.karim, "慢着，大夫——先把手里的灯探进洞口照一照再往里钻。", "dialogue")

    _generate(t, plan)

    assert t.loc(t.alvin) == "scene_2"
    assert t.loc(t.nadia) == "scene_1" and t.loc(t.karim) == "scene_1"
    assert len(t.move_events()) == 1

    # 生成后守卫再跑一遍、KP 事后补调同一工具：都不重复执行。
    pre = session_service.get_next_sequence_num(t.db, t.sid) - 1
    chunks = asyncio.run(_collect(chat_service._ensure_planned_scene(
        t.db, t.sid, t.session, t.module, t.alvin, t.others(), plan, pre_gen_seq=pre,
    )))
    assert chunks == []
    tool_chunks = asyncio.run(_collect(run_text_commands(
        t.db, t.sid, "[SCENE_CHANGE: scene_id=scene_2]", t.module, t.alvin, t.session,
        teammates=t.others(),
    )))
    assert tool_chunks == []
    assert t.loc(t.nadia) == "scene_1"   # 无参数的补调没把劝阻的队友卷进去

    # 重新生成：回滚叙事后重跑（规划器重新出同样的计划）→ 复用本回合裁定，不重复移动/进场。
    session_service.rollback_last_kp_output(t.db, t.sid)
    asyncio.run(chat_service._run_generation(
        t.db, t.sid, t.session, t.module, t.alvin,
        session_service.get_session_events(t.db, t.sid), teammates=t.others(),
    ))
    assert len(t.move_events()) == 1
    assert t.loc(t.alvin) == "scene_2" and t.loc(t.nadia) == "scene_1"
    assert "本轮移动裁定" in _system_text(kp.calls[-1])


async def _collect(agen):
    return [c async for c in agen]


def test_非主角真人独自移动_不挪动主角和其他玩家(db_factory, monkeypatch):
    kp = _KP()
    _runtime(monkeypatch, db_factory, kp)
    t = _seed(db_factory(), human_hassan=True)
    t.say(t.hassan, "我要从拱门钻进去")
    t.say(t.alvin, "我留在这里看壁画")
    plan = _plan(MovePlan(actors=["哈桑"], exit="拱门", target="scene_2"))

    _generate(t, plan)

    assert t.loc(t.hassan) == "scene_2"
    assert t.loc(t.alvin) == "scene_1" and t.session.current_scene_id == "scene_1"
    assert t.loc(t.nadia) == "scene_1" and t.loc(t.karim) == "scene_1"


def test_明确集体移动_带上AI队友_不替沉默的真人玩家决定(db_factory, monkeypatch):
    kp = _KP()
    _runtime(monkeypatch, db_factory, kp)
    t = _seed(db_factory(), human_hassan=True)
    t.say(t.alvin, "我们一起穿过拱门")
    plan = _plan(MovePlan(actors=["阿尔文"], exit="拱门", target="scene_2"))

    _generate(t, plan)

    assert t.loc(t.alvin) == "scene_2"
    assert t.loc(t.nadia) == "scene_2" and t.loc(t.karim) == "scene_2"
    assert t.loc(t.hassan) == "scene_1"   # 真人哈桑本轮没表态：不被代为决定


def test_集体移动_真人自己表态跟上才一同前往(db_factory):
    t = _seed(db_factory(), human_hassan=True)
    t.say(t.alvin, "我们一起穿过拱门")
    t.say(t.hassan, "我跟上")
    chunks, note = movement.adjudicate_plan(
        t.db, t.sid, t.session, t.module, t.alvin, t.others(),
        _plan(MovePlan(actors=["阿尔文"], exit="拱门", target="scene_2")),
    )
    assert chunks and "本轮移动裁定" in note
    assert t.loc(t.hassan) == "scene_2"


@pytest.mark.parametrize("text, status", [
    ("我们要不要进去？", "discuss"),
    ("如果门打开，我就进去", "conditional"),
    ("我走近拱门观察", "approach"),
    ("我们之后可能从拱门进去看看", "discuss"),
])
def test_讨论_条件句_走近观察_不切场景_KP工具也被拦下(db_factory, text, status):
    t = _seed(db_factory())
    t.say(t.alvin, text)
    # 规划器误判成行动（最坏情况）：原话复核仍不放行。
    plan = _plan(MovePlan(actors=["阿尔文"], exit="拱门", target="scene_2"), scene_change="scene_2")
    chunks, note = movement.adjudicate_plan(
        t.db, t.sid, t.session, t.module, t.alvin, t.others(), plan,
    )
    assert chunks == []
    assert t.loc(t.alvin) == "scene_1"
    assert movement.turn_records(t.session)[0]["status"] == status
    assert "本轮不移动" in note or "仍在" in note
    tool_chunks = asyncio.run(_collect(run_text_commands(
        t.db, t.sid, "[SCENE_CHANGE: scene_id=scene_2]", t.module, t.alvin, t.session,
        teammates=t.others(),
    )))
    assert tool_chunks == [] and t.loc(t.alvin) == "scene_1"


def test_我要也是行动宣言():
    assert movement.analyze("我要从拱门钻进去").kind == "act"
    assert movement.analyze("我抛下队友，从拱门钻进去").solo is True
    assert movement.analyze("我钻进去。你们要不要跟上？").kind == "act"
    assert movement.analyze("我钻进去之后打开手电").kind == "act"


def test_前室多道拱门指代不清_澄清不移动_说明方位后才走(db_factory):
    t = _seed(db_factory(), start="scene_2")
    t.say(t.alvin, "我从拱门钻进去")
    chunks, note = movement.adjudicate_plan(
        t.db, t.sid, t.session, t.module, t.alvin, t.others(),
        _plan(MovePlan(actors=["阿尔文"], exit="拱门", target="scene_3")),
    )
    assert chunks == [] and t.loc(t.alvin) == "scene_2"
    assert "不止一处" in note and "耳室" not in note.split("（")[0]
    # KP 擅自替玩家挑一道也不行
    asyncio.run(_collect(run_text_commands(
        t.db, t.sid, "[SCENE_CHANGE: scene_id=scene_3]", t.module, t.alvin, t.session,
        teammates=t.others(),
    )))
    assert t.loc(t.alvin) == "scene_2"

    dest = movement.resolve_destination(t.module, "scene_2", "我穿过北侧拱门")
    assert (dest.status, dest.target) == ("resolved", "scene_3")


def test_通道受阻不移动(db_factory):
    t = _seed(db_factory(), start="scene_2")
    t.say(t.alvin, "我推开石板，从东侧拱门挤进墓室")
    chunks, note = movement.adjudicate_plan(
        t.db, t.sid, t.session, t.module, t.alvin, t.others(),
        _plan(MovePlan(actors=["阿尔文"], exit="东侧拱门", target="scene_4",
                       blocked="巨大石板挡住了拱门，徒手推不动", check_skill="力量")),
    )
    assert chunks == [] and t.loc(t.alvin) == "scene_2"
    assert "石板" in note
    record = movement.turn_records(t.session)[0]
    assert record["status"] == "blocked" and not record["skill"]
    assert not (t.session.turn_state or {}).get(movement.PENDING_KEY)


def test_不连通的目标_拒绝并解释(db_factory):
    t = _seed(db_factory())
    t.say(t.alvin, "我直接穿过去进入藏宝室")
    chunks, note = movement.adjudicate_plan(
        t.db, t.sid, t.session, t.module, t.alvin, t.others(),
        _plan(MovePlan(actors=["阿尔文"], target="scene_5")),
    )
    assert chunks == [] and t.loc(t.alvin) == "scene_1"
    assert movement.turn_records(t.session)[0]["status"] == "unreachable"
    assert "到不了" in note


# ── 检定门控 ──────────────────────────────────────────────────────────


def _roll(t: Tomb, char, skill: str, outcome: str):
    """走玩家手动投骰的真实结算链（真人 KP 模式下到结算为止，不触发 KP 续写）。"""
    gs = t.session
    gs.kp_mode = "human"
    t.db.commit()
    result = SimpleNamespace(tier=outcome, outcome=outcome, description=f"{skill} {outcome}")
    asyncio.run(turn_orchestrator._settle_rolled_check(
        t.db, t.sid, gs, t.module, t.alvin, t.others(),
        check={"char_id": char.id}, result=result, skill=skill, difficulty="normal",
        source="", disp_name=char.name,
    ))


def test_需检定_未投不动_无关投骰不触发_失败不动_成功才执行对应移动(db_factory):
    t = _seed(db_factory())
    t.say(t.alvin, "我抛下队友，从墙洞爬进去")
    plan = _plan(MovePlan(actors=["阿尔文"], exit="墙洞", target="scene_2", solo=True,
                          check_skill="攀爬"))
    chunks, note = movement.adjudicate_plan(
        t.db, t.sid, t.session, t.module, t.alvin, t.others(), plan,
    )
    assert chunks == [] and t.loc(t.alvin) == "scene_1"
    assert "攀爬" in note and "不要提前描写新空间" in note

    _roll(t, t.alvin, "侦查", "success")          # 无关技能
    _roll(t, t.nadia, "攀爬", "success")          # 别人的同名检定
    assert t.loc(t.alvin) == "scene_1"
    _roll(t, t.alvin, "攀爬", "failure")
    assert t.loc(t.alvin) == "scene_1"
    # 失败即作废：之后再投成功也不动
    _roll(t, t.alvin, "攀爬", "success")
    assert t.loc(t.alvin) == "scene_1"


def test_需检定_成功后只执行对应移动(db_factory):
    t = _seed(db_factory())
    t.say(t.alvin, "我抛下队友，从墙洞爬进去")
    movement.adjudicate_plan(
        t.db, t.sid, t.session, t.module, t.alvin, t.others(),
        _plan(MovePlan(actors=["阿尔文"], exit="墙洞", target="scene_2", solo=True,
                       check_skill="攀爬")),
    )
    _roll(t, t.alvin, "攀爬", "success")
    assert t.loc(t.alvin) == "scene_2"
    assert all(t.loc(c) == "scene_1" for c in t.others())
    assert movement.turn_records(t.session)[0]["status"] == movement.MOVED


def test_过期提案_新回合后的投骰不兑现(db_factory):
    t = _seed(db_factory())
    t.say(t.alvin, "我从墙洞爬进去")
    movement.adjudicate_plan(
        t.db, t.sid, t.session, t.module, t.alvin, t.others(),
        _plan(MovePlan(actors=["阿尔文"], exit="墙洞", target="scene_2", check_skill="攀爬")),
    )
    check_gating.begin_turn(t.db, t.session)   # 玩家没投就又行动了
    _roll(t, t.alvin, "攀爬", "success")
    assert t.loc(t.alvin) == "scene_1"


def test_AI队友自动掷检定也走同一门控(db_factory):
    t = _seed(db_factory())
    t.say(t.nadia, "我从墙洞爬进去，你们留在这儿")
    movement.adjudicate_plan(
        t.db, t.sid, t.session, t.module, t.alvin, t.others(),
        _plan(MovePlan(actors=["纳迪娅"], exit="墙洞", target="scene_2", solo=True,
                       check_skill="攀爬")),
    )
    asyncio.run(_collect(run_text_commands(
        t.db, t.sid, "[DICE_CHECK: skill=攀爬, char=纳迪娅]", t.module, t.alvin, t.session,
        teammates=t.others(),
    )))
    assert t.loc(t.nadia) == "scene_2"   # 攀爬 99 自动掷成功
    assert t.loc(t.alvin) == "scene_1" and t.session.current_scene_id == "scene_1"


def test_KP漏挂门控检定_后置守卫确定性补挂(db_factory, monkeypatch):
    kp = _KP()
    _runtime(monkeypatch, db_factory, kp)
    t = _seed(db_factory())
    t.say(t.alvin, "我从墙洞爬进去")
    plan = _plan(MovePlan(actors=["阿尔文"], exit="墙洞", target="scene_2", check_skill="攀爬"),
                 requires_check=True, check={"skill": "攀爬"})
    _generate(t, plan)
    pending = [e for e in session_service.get_session_events(t.db, t.sid)
               if (e.metadata_ or {}).get("check_request")]
    assert pending and pending[0].metadata_["skill"] == "攀爬"
    assert t.loc(t.alvin) == "scene_1"


# ── 原生工具与文本适配器 ──────────────────────────────────────────────


@pytest.mark.parametrize("native", [True, False])
def test_原生工具与文本适配器_同一意图位置结果一致(db_factory, monkeypatch, native):
    call = ToolCall(id="c1", name="scene_change", arguments={"scene_id": "前室", "movers": "阿尔文"})
    kp = _KP(
        scripts=[[StreamDelta(kind="text", text="他钻了进去。"), StreamDelta(kind="tool_call", tool_call=call)],
                 [StreamDelta(kind="text", text="")]],
        text_script="他钻了进去。[SCENE_CHANGE: scene_id=前室, movers=阿尔文]",
    )
    _runtime(monkeypatch, db_factory, kp, native=native)
    t = _seed(db_factory())
    t.say(t.alvin, "我抛下队友，从拱门钻进去")
    _generate(t, TurnPlan())   # 规划器什么也没给：全靠 KP 的工具调用

    assert t.loc(t.alvin) == "scene_2"
    assert [t.loc(c) for c in t.others()] == ["scene_1"] * 3
    assert len(t.move_events()) == 1


# ── 新位置下的 NPC、进场效果与大地图 ───────────────────────────────────


def test_移动后_NPC在场与进场检定按新位置_只给移动者(db_factory, monkeypatch):
    scenes = copy.deepcopy(TOMB_SCENES)
    scenes[1]["events"] = [{"trigger": "进入前室时", "kind": "dice_check", "skill": "聆听"}]
    npcs = [{"id": "guard", "name": "兽首守卫", "initial_location": "scene_2",
             "description": "长矛贯身的木乃伊"}]
    kp = _KP()
    _runtime(monkeypatch, db_factory, kp)
    t = _seed(db_factory(), npcs=npcs, scenes=scenes)
    t.say(t.alvin, "我抛下队友，从拱门钻进去")
    _generate(t, _plan(MovePlan(actors=["阿尔文"], exit="拱门", target="scene_2", solo=True)))

    by_group = {_group_of(m): _system_text(m) for m in kp.calls}
    assert set(by_group) == {"前室", "甬道"}
    for text in by_group.values():   # 两组看到的人员分布一致：守卫只与阿尔文同处
        assert "- 前室：阿尔文；在场 NPC：兽首守卫" in text
        assert "- 甬道：纳迪娅、卡里姆、哈桑\n" in text
    checks = [e for e in session_service.get_session_events(t.db, t.sid)
              if (e.metadata_ or {}).get("check_request")]
    assert [c.metadata_["char_id"] for c in checks] == [t.alvin.id]   # 只有到了前室的人


def test_大地图前往_同一执行语义_AI默认跟随_真人不被代为决定(db_factory):
    t = _seed(db_factory(), human_hassan=True)
    movement.travel(t.db, t.sid, t.session, t.module, t.alvin, "scene_2", t.party)
    assert t.loc(t.alvin) == "scene_2"
    assert t.loc(t.nadia) == "scene_2" and t.loc(t.karim) == "scene_2"
    assert t.loc(t.hassan) == "scene_1"
    # 非房主前往只搬他自己
    movement.travel(t.db, t.sid, t.session, t.module, t.hassan, "scene_2", t.party)
    movement.travel(t.db, t.sid, t.session, t.module, t.hassan, "scene_1", t.party)
    assert t.loc(t.hassan) == "scene_1" and t.loc(t.alvin) == "scene_2"


def test_规划器移动字段容错():
    plan = TurnPlan.model_validate({"scene_policy": {"moves": {"actors": "阿尔文", "solo": "true",
                                                               "stance": "瞎写", "exit": ["拱门"]}}})
    move = plan.scene_policy.moves[0]
    assert move.actors == ["阿尔文"] and move.solo is True and move.stance == "act"
    assert TurnPlan.model_validate({"scene_policy": {"moves": "不知道"}}).scene_policy.moves == []


def test_模组可选的显式出口声明优先于描述推断(db_factory):
    scenes = copy.deepcopy(TOMB_SCENES)
    scenes[1]["exits"] = [
        {"to": "scene_3", "name": "北拱门", "aliases": ["小门"]},
        {"to": "scene_4", "name": "石板拱门"},
    ]
    t = _seed(db_factory(), scenes=scenes)
    assert movement.resolve_destination(t.module, "scene_2", "我从小门钻进去").target == "scene_3"
    assert movement.resolve_destination(t.module, "scene_2", "我穿过拱门").status == "ambiguous"


def test_kp_tool_loop_movers参数可解析():
    calls = kp_tool_loop._tool_calls_from_text("[SCENE_CHANGE: scene_id=scene_2, movers=阿尔文、纳迪娅]")
    assert calls[0].arguments == {"scene_id": "scene_2", "movers": "阿尔文、纳迪娅"}


def test_重复提案只执行一次(db_factory):
    t = _seed(db_factory())
    t.say(t.alvin, "我抛下队友，从拱门钻进去")
    move = MovePlan(actors=["阿尔文"], exit="拱门", target="scene_2", solo=True)
    movement.adjudicate_plan(t.db, t.sid, t.session, t.module, t.alvin, t.others(), _plan(move, move))
    movement.adjudicate_plan(t.db, t.sid, t.session, t.module, t.alvin, t.others(), _plan(move))
    assert len(t.move_events()) == 1
    assert len(movement.turn_records(t.session)) == 1


def test_生成被取消后_位置与存档一致_重新生成不重复移动(db_factory, monkeypatch):
    class _Cancelling(_KP):
        async def stream_chat(self, messages, tools=None, temperature=0.7, max_tokens=None):
            self.calls.append(messages)
            raise asyncio.CancelledError()
            yield  # pragma: no cover

    plan = _plan(MovePlan(actors=["阿尔文"], exit="拱门", target="scene_2", solo=True))
    _runtime(monkeypatch, db_factory, _Cancelling(), plan_for_regen=plan)
    t = _seed(db_factory())
    t.say(t.alvin, "我抛下队友，从拱门钻进去")
    with pytest.raises(asyncio.CancelledError):
        _generate(t, plan)
    # 落位发生在动笔前：取消不回滚位置，存档里的 move 事件与位置一致。
    assert t.loc(t.alvin) == "scene_2" and len(t.move_events()) == 1

    kp = _KP()
    monkeypatch.setattr(chat_service, "get_llm", lambda: kp)
    monkeypatch.setattr(chat_service, "get_fast_llm", lambda: kp)
    session_service.rollback_last_kp_output(t.db, t.sid)
    asyncio.run(chat_service._run_generation(
        t.db, t.sid, t.session, t.module, t.alvin,
        session_service.get_session_events(t.db, t.sid), teammates=t.others(),
    ))
    assert len(t.move_events()) == 1
    assert t.loc(t.alvin) == "scene_2" and all(t.loc(c) == "scene_1" for c in t.others())


def test_规划器输入带各成员位置与出口邻居_提示区分行动宣言与商量(db_factory):
    import json as _json

    t = _seed(db_factory(), human_hassan=True)
    session_service.set_char_location(t.db, t.sid, t.hassan.id, "scene_2")
    messages = turn_planner.build_turn_plan_messages(
        t.session, t.module, t.alvin, session_service.get_session_events(t.db, t.sid),
        teammates=t.others(),
    )
    user = messages[-1]["content"]
    payload = _json.loads(user[user.index("{\"module\""):user.rindex("}") + 1].split("\n\n")[0])
    positions = {p["name"]: p for p in payload["party_positions"]}
    assert positions["哈桑"]["scene_id"] == "scene_2"
    assert positions["哈桑"]["neighbors"] == ["scene_1", "scene_3", "scene_4"]
    assert positions["阿尔文"]["scene_id"] == "scene_1"
    assert "scene_policy.moves" in user and "「我要」不是商量" in user


def test_真人KP切场景_默认整队_可点名移动者(db_factory):
    from app.services import human_kp_actions

    t = _seed(db_factory(), human_hassan=True)
    asyncio.run(human_kp_actions.execute_human_kp_action(
        t.db, t.sid, t.session, t.module, "scene_change", {"scene_id": "scene_2", "movers": "哈桑"},
    ))
    assert t.loc(t.hassan) == "scene_2" and t.loc(t.alvin) == "scene_1"
    asyncio.run(human_kp_actions.execute_human_kp_action(
        t.db, t.sid, t.session, t.module, "scene_change", {"scene_id": "scene_2"},
    ))
    assert [t.loc(c) for c in t.party] == ["scene_2"] * 4


@pytest.mark.parametrize("text, acts", [
    # 本地《陵墓》存档 27af5ba9 的真实原话
    ("我不想和这三个书呆子多说，直接从这个缺口钻进去", True),
    ("等一下，大夫——我看了那个洞口的凿痕：刃口全都朝我们这一侧翻起。别急着一头钻进去，先让灯照到另一侧再抬脚。", False),
    ("别急着往里钻。这洞是从我们这面凿穿的，碎屑全堆在甬道里——凿它的人进去了，可没见出来。", False),
    ("慢着，大夫——先把手里的灯探进洞口照一照再往里钻。这洞是外头的人凿穿的，凿的人没准也想从这一头出去。", False),
    ("别慌——那面墙上的洞是新凿的，有人在我们之前进去过，也就说明有路。我去看看那个洞口。", False),
    ("我蹲到拱门左下角，从口袋里摸出放大镜——这凿子是从我们这一面打进去的，还是从洞里面往外凿开的。", False),
    ("我把灯压到那两条平行浅槽上方，火线贴着槽底扫出去，想看清那几粒反光的小圆东西是什么。", False),
    ("我侧身贴住洞口下沿，先探一条腿过去、脚跟横着蹭实了才压上重量，钻进这间屋子后立刻蹲到卡里姆侧后半步，"
     "低头去看浮灰上那两道浅槽——它们往哪边去、间距多宽，能不能容下一件抬着走的东西。", True),
])
def test_真实存档原话的移动判读(text, acts):
    assert movement.analyze(text).acts is acts


# ══ 验收复现：多人检定分别结算 / 工具省略 movers / 原子提交 ══════════════════


@pytest.fixture
def fixed_d100(monkeypatch):
    """钉死骰值：50。纳迪娅（攀爬 99）必成，卡里姆（攀爬 1）必败，阿尔文（50）恰好成功。"""
    from app.rules.coc.components import check as coc_check

    monkeypatch.setattr(coc_check, "roll_percentile", lambda: 50)


def _fresh(t: Tomb):
    """新的数据库会话：只看已提交的结果。"""
    db = t.factory()
    gs = db.get(GameSession, t.sid)
    return db, gs


def _locs(t: Tomb) -> dict:
    db, gs = _fresh(t)
    try:
        return {c.name: session_service.get_char_location(gs, c.id) for c in t.party}
    finally:
        db.close()


def _committed_state(t: Tomb) -> tuple[dict, list, list, list]:
    db, gs = _fresh(t)
    try:
        moves = [e for e in session_service.get_session_events(db, t.sid)
                 if (e.metadata_ or {}).get("kind") == "move"]
        ts = gs.turn_state or {}
        locs = {c.name: session_service.get_char_location(gs, c.id) for c in t.party}
        return locs, moves, list(ts.get(movement.MOVES_KEY) or []), list(ts.get(movement.PENDING_KEY) or [])
    finally:
        db.close()


def _seed_f(db_factory, **kw) -> Tomb:
    t = _seed(db_factory(), **kw)
    t.factory = db_factory
    return t


def _stage_climb(t: Tomb, actors: list[str], *, shared=False, skill="攀爬"):
    return movement.adjudicate_plan(
        t.db, t.sid, t.session, t.module, t.alvin, t.others(),
        _plan(MovePlan(actors=actors, exit="墙洞", target="scene_2", check_skill=skill,
                       shared_check=shared)),
    )


def test_两人各自攀爬_A成功只移动A_B继续待投_B失败不影响A(db_factory):
    t = _seed_f(db_factory, human_hassan=True)
    t.say(t.alvin, "我和哈桑从墙洞爬进去，你们留在这儿")
    t.say(t.hassan, "我也从墙洞爬进去")
    _stage_climb(t, ["阿尔文", "哈桑"])

    _roll(t, t.alvin, "攀爬", "success")
    assert _locs(t) == {"阿尔文": "scene_2", "纳迪娅": "scene_1", "卡里姆": "scene_1", "哈桑": "scene_1"}
    pending = (t.session.turn_state or {})[movement.PENDING_KEY]
    assert pending and pending[0]["chars"][t.hassan.id] == "pending"

    _roll(t, t.hassan, "攀爬", "failure")
    locs, moves, records, pending = _committed_state(t)
    assert locs["阿尔文"] == "scene_2" and locs["哈桑"] == "scene_1"
    assert len(moves) == 1 and pending == []
    assert records[0]["char_status"] == {t.alvin.id: "moved", t.hassan.id: "failed"}


def test_门控结算写回裁定记录_保留扩展字段_不改无关旧记录(db_factory):
    """结算只拥有个人状态与整条状态：其他字段（含未知扩展）原样保留，无关的旧格式记录逐字不变。"""
    t = _seed_f(db_factory, human_hassan=True)
    t.say(t.alvin, "我和哈桑从墙洞爬进去，你们留在这儿")
    t.say(t.hassan, "我也从墙洞爬进去")
    _stage_climb(t, ["阿尔文", "哈桑"])
    gs = t.session
    ts = dict(gs.turn_state or {})
    legacy = {"anchor": -99, "status": "discuss", "movers": [t.party[2].id]}   # 无 char_status 的旧记录
    ts[movement.MOVES_KEY] = [dict(r, ext={"来源": ["插件"]}) for r in ts[movement.MOVES_KEY]] + [legacy]
    gs.turn_state = ts
    t.db.commit()

    _roll(t, t.alvin, "攀爬", "success")
    _locs_, _moves, records, _pending = _committed_state(t)
    assert records[0]["ext"] == {"来源": ["插件"]}
    assert records[0]["char_status"] == {t.alvin.id: "moved", t.hassan.id: "pending"}
    assert records[0]["status"] == movement.PENDING
    assert records[1] == legacy
    assert movement.busy_ids(t.session) == {t.alvin.id, t.hassan.id, t.party[2].id}


def test_A失败后B仍能完成自己的检定与移动(db_factory):
    t = _seed_f(db_factory, human_hassan=True)
    t.say(t.alvin, "我和哈桑从墙洞爬进去，你们留在这儿")
    t.say(t.hassan, "我也从墙洞爬进去")
    _stage_climb(t, ["阿尔文", "哈桑"])
    _roll(t, t.alvin, "攀爬", "failure")
    assert _locs(t)["阿尔文"] == "scene_1"
    _roll(t, t.hassan, "攀爬", "success")
    locs = _locs(t)
    assert locs["哈桑"] == "scene_2" and locs["阿尔文"] == "scene_1"
    _roll(t, t.alvin, "攀爬", "success")   # 失败过的人重投不兑现（失败即结束他这次尝试）
    assert _locs(t)["阿尔文"] == "scene_1"


def test_全自动群检一成一败_各按自己的结果移动(db_factory, fixed_d100):
    t = _seed_f(db_factory)
    t.say(t.nadia, "我从墙洞爬进去")
    t.say(t.karim, "我也从墙洞爬进去")
    t.say(t.alvin, "我留在这里看壁画")
    _stage_climb(t, ["纳迪娅", "卡里姆"])
    asyncio.run(_collect(run_text_commands(
        t.db, t.sid, "[DICE_CHECK: skill=攀爬, chars=纳迪娅、卡里姆]", t.module, t.alvin, t.session,
        teammates=t.others(),
    )))
    locs = _locs(t)
    assert locs["纳迪娅"] == "scene_2" and locs["卡里姆"] == "scene_1" and locs["阿尔文"] == "scene_1"


def test_真人与AI混合群检_自动结果与待投结果互不覆盖(db_factory, fixed_d100):
    t = _seed_f(db_factory)
    t.say(t.alvin, "我和纳迪娅从墙洞爬进去，卡里姆你们留在这儿")
    t.say(t.nadia, "我也从墙洞爬进去")
    _stage_climb(t, ["阿尔文", "纳迪娅"])
    asyncio.run(_collect(run_text_commands(
        t.db, t.sid, "[DICE_CHECK: skill=攀爬, chars=阿尔文、纳迪娅]", t.module, t.alvin, t.session,
        teammates=t.others(),
    )))
    assert _locs(t)["纳迪娅"] == "scene_2" and _locs(t)["阿尔文"] == "scene_1"   # AI 自动成功，真人待投
    request = next(e for e in session_service.get_session_events(t.db, t.sid)
                   if (e.metadata_ or {}).get("check_request") and e.metadata_.get("char_id") == t.alvin.id)
    gs = t.session
    gs.kp_mode = "human"
    t.db.commit()
    result = SimpleNamespace(tier="failure", outcome="failure", description="攀爬 失败")
    asyncio.run(turn_orchestrator._settle_rolled_check(
        t.db, t.sid, gs, t.module, t.alvin, t.others(), check=dict(request.metadata_),
        result=result, skill="攀爬", difficulty="normal", source="", disp_name=t.alvin.name,
    ))
    locs = _locs(t)
    assert locs["阿尔文"] == "scene_1" and locs["纳迪娅"] == "scene_2"   # 群检汇总的「任一成功」不替他通过


@pytest.mark.parametrize("shared", [True, False])
def test_共同障碍_只有明确声明共享时一人成功全员通过(db_factory, shared):
    t = _seed_f(db_factory, human_hassan=True)
    t.say(t.alvin, "我和哈桑一起把墙洞撬大，从墙洞钻进去，你们留在这儿")
    t.say(t.hassan, "我也从墙洞钻进去")
    _stage_climb(t, ["阿尔文", "哈桑"], shared=shared, skill="力量")
    _roll(t, t.alvin, "力量", "success")
    locs = _locs(t)
    assert locs["阿尔文"] == "scene_2"
    assert locs["哈桑"] == ("scene_2" if shared else "scene_1")


def _kp_runtime_no_plan(monkeypatch, db_factory, *, native: bool, args: dict, text: str):
    call = ToolCall(id="c1", name="scene_change", arguments=args)
    kp = _KP(
        scripts=[[StreamDelta(kind="text", text="他钻了进去。"), StreamDelta(kind="tool_call", tool_call=call)],
                 [StreamDelta(kind="text", text="")]],
        text_script=f"他钻了进去。{text}",
    )
    _runtime(monkeypatch, db_factory, kp, native=native)
    return kp


@pytest.mark.parametrize("native", [True, False])
def test_规划器无提案_玩家独行_scene_change省略movers_只有行动者移动(db_factory, monkeypatch, native):
    _kp_runtime_no_plan(monkeypatch, db_factory, native=native, args={"scene_id": "前室"},
                        text="[SCENE_CHANGE: scene_id=前室]")
    t = _seed_f(db_factory)
    t.say(t.alvin, "我抛下队友，从拱门钻进去")
    _generate(t, TurnPlan())
    locs, moves, _records, _pending = _committed_state(t)
    assert locs == {"阿尔文": "scene_2", "纳迪娅": "scene_1", "卡里姆": "scene_1", "哈桑": "scene_1"}
    assert len(moves) == 1
    # 留守者没有显式位置记录也被写实，不随 current_scene_id 漂走
    db, gs = _fresh(t)
    explicit = session_service.get_party_locations(gs)
    db.close()
    assert explicit[t.nadia.id] == "scene_1" and explicit[t.karim.id] == "scene_1"


@pytest.mark.parametrize("native", [True, False])
def test_非房主真人独行_省略movers_只移动他本人(db_factory, monkeypatch, native):
    _kp_runtime_no_plan(monkeypatch, db_factory, native=native, args={"scene_id": "前室"},
                        text="[SCENE_CHANGE: scene_id=前室]")
    t = _seed_f(db_factory, human_hassan=True)
    t.say(t.hassan, "我抛下你们，从拱门钻进去")
    _generate(t, TurnPlan())
    locs = _locs(t)
    assert locs == {"阿尔文": "scene_1", "纳迪娅": "scene_1", "卡里姆": "scene_1", "哈桑": "scene_2"}
    assert t.session.current_scene_id == "scene_1"


@pytest.mark.parametrize("native", [True, False])
def test_无效movers_拒绝而不是回退成主角或整队(db_factory, monkeypatch, native):
    _kp_runtime_no_plan(monkeypatch, db_factory, native=native,
                        args={"scene_id": "前室", "movers": "不存在的人"},
                        text="[SCENE_CHANGE: scene_id=前室, movers=不存在的人]")
    t = _seed_f(db_factory)
    t.say(t.alvin, "我从拱门钻进去")
    _generate(t, TurnPlan())
    locs, moves, _r, _p = _committed_state(t)
    assert set(locs.values()) == {"scene_1"} and moves == []


def test_省略movers不能绕过已有的门控与阻挡裁定(db_factory):
    t = _seed_f(db_factory)
    t.say(t.alvin, "我抛下队友，从墙洞爬进去")
    _stage_climb(t, ["阿尔文"])
    asyncio.run(_collect(run_text_commands(
        t.db, t.sid, "[SCENE_CHANGE: scene_id=前室]", t.module, t.alvin, t.session, teammates=t.others(),
    )))
    assert _locs(t)["阿尔文"] == "scene_1"


def test_真人KP无效movers报错不整队移动(db_factory):
    from app.services import human_kp_actions

    t = _seed_f(db_factory)
    with pytest.raises(ValueError):
        asyncio.run(human_kp_actions.execute_human_kp_action(
            t.db, t.sid, t.session, t.module, "scene_change", {"scene_id": "scene_2", "movers": "路人甲"},
        ))
    assert set(_locs(t).values()) == {"scene_1"}


# ── 原子提交 ──────────────────────────────────────────────────────────


def _solo_plan():
    return _plan(MovePlan(actors=["阿尔文"], exit="拱门", target="scene_2", solo=True))


def _fail_on(monkeypatch, module_obj, name, predicate):
    real = getattr(module_obj, name)

    def flaky(*a, **k):
        if predicate(*a, **k):
            raise RuntimeError(f"注入：{name} 写入失败")
        return real(*a, **k)

    monkeypatch.setattr(module_obj, name, flaky)
    return lambda: monkeypatch.setattr(module_obj, name, real)


@pytest.mark.parametrize("where", ["event", "records", "position"])
def test_移动中途写入失败_位置事件凭据一起回滚_之后可安全重试(db_factory, monkeypatch, where):
    t = _seed_f(db_factory)
    t.say(t.alvin, "我抛下队友，从拱门钻进去")
    if where == "event":
        restore = _fail_on(monkeypatch, session_service, "add_event",
                           lambda *a, **k: (k.get("metadata") or {}).get("kind") == "move")
    elif where == "records":
        restore = _fail_on(monkeypatch, movement, "_set_turn_state", lambda *a, **k: True)
    else:
        calls = {"n": 0}

        def second(*a, **k):
            calls["n"] += 1
            return calls["n"] >= 2
        restore = _fail_on(monkeypatch, session_service, "set_char_location", second)

    with pytest.raises(RuntimeError):
        movement.adjudicate_plan(t.db, t.sid, t.session, t.module, t.alvin, t.others(), _solo_plan())
    locs, moves, records, pending = _committed_state(t)
    assert set(locs.values()) == {"scene_1"} and moves == [] and records == [] and pending == []
    db, gs = _fresh(t)
    assert session_service.get_party_locations(gs) == {} and gs.current_scene_id == "scene_1"
    db.close()

    restore()
    t.db.rollback()
    movement.adjudicate_plan(t.db, t.sid, t.session, t.module, t.alvin, t.others(), _solo_plan())
    locs, moves, records, _pending = _committed_state(t)
    assert locs["阿尔文"] == "scene_2" and locs["纳迪娅"] == "scene_1"
    assert len(moves) == 1 and records[0]["status"] == movement.MOVED


def test_门控结算写入失败_不丢提案_重试成功只执行一次(db_factory, monkeypatch):
    t = _seed_f(db_factory)
    t.say(t.alvin, "我抛下队友，从墙洞爬进去")
    _stage_climb(t, ["阿尔文"])
    restore = _fail_on(monkeypatch, session_service, "add_event",
                       lambda *a, **k: (k.get("metadata") or {}).get("kind") == "move")
    with pytest.raises(RuntimeError):
        _roll(t, t.alvin, "攀爬", "success")
    locs, moves, _records, pending = _committed_state(t)
    assert locs["阿尔文"] == "scene_1" and moves == []
    assert pending and pending[0]["chars"][t.alvin.id] == "pending"

    restore()
    t.db.rollback()
    _roll(t, t.alvin, "攀爬", "success")
    _roll(t, t.alvin, "攀爬", "success")   # 重复结算同一次投骰：不重复执行
    locs, moves, records, pending = _committed_state(t)
    assert locs["阿尔文"] == "scene_2" and len(moves) == 1 and pending == []
    assert records[0]["status"] == movement.MOVED


def test_配图失败不回滚已提交的移动也不重复写事件(db_factory, monkeypatch):
    from app.services import illustration_service

    def boom(*a, **k):
        raise RuntimeError("配图服务挂了")

    monkeypatch.setattr(illustration_service, "_maybe_scene_illustration", boom)
    t = _seed_f(db_factory)
    t.say(t.alvin, "我抛下队友，从拱门钻进去")
    chunks, _note = movement.adjudicate_plan(
        t.db, t.sid, t.session, t.module, t.alvin, t.others(), _solo_plan(),
    )
    assert chunks
    movement.adjudicate_plan(t.db, t.sid, t.session, t.module, t.alvin, t.others(), _solo_plan())
    locs, moves, _r, _p = _committed_state(t)
    assert locs["阿尔文"] == "scene_2" and len(moves) == 1


def _scene_change_tool(t: Tomb, text: str):
    return asyncio.run(_collect(run_text_commands(
        t.db, t.sid, text, t.module, t.alvin, t.session, teammates=t.others(),
    )))


def test_共同障碍_先失败后成功_失败者也一起通过_工具守卫不再拦他(db_factory):
    t = _seed_f(db_factory, human_hassan=True)
    t.say(t.alvin, "我和哈桑一起把墙洞撬大，从墙洞钻进去，你们留在这儿")
    t.say(t.hassan, "我也从墙洞钻进去")
    _stage_climb(t, ["阿尔文", "哈桑"], shared=True, skill="力量")

    _roll(t, t.alvin, "力量", "failure")
    locs, moves, records, pending = _committed_state(t)
    assert locs["阿尔文"] == "scene_1" and moves == []
    assert pending and pending[0]["chars"] == {t.alvin.id: "tried", t.hassan.id: "pending"}
    assert records[0]["status"] == movement.PENDING
    _roll(t, t.alvin, "力量", "success")   # 他这次尝试已用掉：重投不算数
    assert _locs(t)["阿尔文"] == "scene_1"

    _roll(t, t.hassan, "力量", "success")
    locs, moves, records, pending = _committed_state(t)
    assert locs["阿尔文"] == "scene_2" and locs["哈桑"] == "scene_2"
    assert locs["纳迪娅"] == "scene_1" and locs["卡里姆"] == "scene_1"
    assert len(moves) == 1 and pending == []
    assert records[0]["status"] == movement.MOVED
    assert records[0]["char_status"] == {t.alvin.id: "moved", t.hassan.id: "moved"}
    # KP 事后补调 scene_change 点名阿尔文：按「已落位」幂等放行，不再依据先前的失败拒绝
    chunks = _scene_change_tool(t, "[SCENE_CHANGE: scene_id=前室, movers=阿尔文]")
    assert chunks == [] and len(_committed_state(t)[1]) == 1
    assert movement.tool_guard(t.session, "scene_2", [t.alvin]) == ("", [t.alvin.id])


def test_共同障碍_工具路径在障碍解除后放行先前失败者(db_factory):
    """障碍解除后，KP 的 scene_change 才搬人（这条移动没有被结算掉的情形）：守卫不能拿 tried 当失败拦他。"""
    t = _seed_f(db_factory, human_hassan=True)
    t.say(t.alvin, "我和哈桑一起把墙洞撬大，从墙洞钻进去，你们留在这儿")
    t.say(t.hassan, "我也从墙洞钻进去")
    _stage_climb(t, ["阿尔文", "哈桑"], shared=True, skill="力量")
    _roll(t, t.alvin, "力量", "failure")
    # 障碍还在：A 仍是「待检定」，工具被拦（而不是被判成失败）
    refusal, _ = movement.tool_guard(t.session, "scene_2", [t.alvin])
    assert "检定" in refusal and "失败" not in refusal
    _roll(t, t.hassan, "力量", "success")
    assert _locs(t)["阿尔文"] == "scene_2"


def test_共同障碍_所有人都试过且失败_全体失败_提案结束(db_factory):
    t = _seed_f(db_factory, human_hassan=True)
    t.say(t.alvin, "我和哈桑一起把墙洞撬大，从墙洞钻进去，你们留在这儿")
    t.say(t.hassan, "我也从墙洞钻进去")
    _stage_climb(t, ["阿尔文", "哈桑"], shared=True, skill="力量")
    _roll(t, t.alvin, "力量", "failure")
    _roll(t, t.hassan, "力量", "failure")
    locs, moves, records, pending = _committed_state(t)
    assert locs["阿尔文"] == "scene_1" and locs["哈桑"] == "scene_1" and moves == []
    assert pending == [] and records[0]["status"] == "failed"
    _scene_change_tool(t, "[SCENE_CHANGE: scene_id=前室, movers=阿尔文]")
    assert _locs(t)["阿尔文"] == "scene_1"


def test_个人通行_先失败后他人成功_失败者仍不动_工具也拦(db_factory):
    t = _seed_f(db_factory, human_hassan=True)
    t.say(t.alvin, "我和哈桑从墙洞爬进去，你们留在这儿")
    t.say(t.hassan, "我也从墙洞爬进去")
    _stage_climb(t, ["阿尔文", "哈桑"])
    _roll(t, t.alvin, "攀爬", "failure")
    _roll(t, t.hassan, "攀爬", "success")
    locs, _moves, records, _pending = _committed_state(t)
    assert locs["阿尔文"] == "scene_1" and locs["哈桑"] == "scene_2"
    assert records[0]["char_status"] == {t.alvin.id: "failed", t.hassan.id: "moved"}
    _scene_change_tool(t, "[SCENE_CHANGE: scene_id=前室, movers=阿尔文]")
    assert _locs(t)["阿尔文"] == "scene_1"


def test_工具守卫读旧格式裁定记录_无个人状态时按整条状态拦截或放行():
    a, b = SimpleNamespace(id="a", name="甲"), SimpleNamespace(id="b", name="乙")
    gs = SimpleNamespace(turn_state={movement.MOVES_KEY: [
        {"anchor": 1, "status": "discuss", "movers": ["a"], "mover_names": ["甲"], "target": None},
        {"anchor": 1, "status": movement.PENDING, "movers": ["b"], "target": "room", "skill": "攀爬"},
    ]})
    refusal, _ = movement.tool_guard(gs, "room", [a])
    assert refusal.startswith("本轮移动裁定为不移动（玩家只是在商量）：甲")
    refusal, _ = movement.tool_guard(gs, "room", [b])
    assert "「攀爬」检定" in refusal
    gs.turn_state[movement.MOVES_KEY][1]["char_status"] = {"b": "moved"}
    assert movement.tool_guard(gs, "room", [b]) == ("", ["b"])
    assert movement.char_status(gs.turn_state[movement.MOVES_KEY][1], "b") == movement.MOVED
