"""KP 两条执行路径的**等价性契约**。

项目刻意保留了两条把「模型意图」变成「世界状态」的路径（DESIGN §8.3、§13-5）：

- **工具路径**：模型走标准 tool call，由 ``_build_kp_tool_executor`` 分发；
- **文本兼容路径**：模型把指令写成 ``[DICE_CHECK: ...]`` 方括号，由 ``_process_commands`` 解析。

保留降级路径是自觉的取舍（供应商不支持工具调用时不能没得玩）。但此前**只有各自的用例
测试，没有任何东西保证两条路径行为一致**——同一个意图走不同路径落库出不同结果，
是这种双实现结构最典型、也最难在跑团现场发现的缺陷（玩家只会觉得「今天 KP 怪怪的」）。

这组测试就是那份缺失的契约：同一意图分别喂给两条路径，断言**落库事件与世界状态一致**。

**口径**：比对「事件类型 + actor + 关键内容」与受影响的世界状态，不比对旁白措辞
（旁白由模型产出，本就不同）、不比对事件 id/时间戳（本就该不同）。
新增一个改状态的工具时，请在 ``INTENTS`` 里加一条——那是这份契约的清单。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.ai.provider import ToolCall
from app.models.base import Base
from app.models.character import Character
from app.models.event_log import EventLog
from app.models.module import Module
from app.models.session import GameSession
from app.models.session_participant import SessionParticipant  # noqa: F401 — 注册建表
from app.services import kp_tool_loop, session_service


@pytest.fixture
def db_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'dual.db'}", connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed(db):
    module = Module(
        title="等价性测试模组", rule_system="coc",
        scenes=[
            {"id": "scene_a", "title": "书房", "kind": "location", "connections": ["scene_b"]},
            {"id": "scene_b", "title": "地下室", "kind": "location", "connections": ["scene_a"]},
        ],
        npcs=[{"id": "n_1", "name": "管家"}],
        clues=[], triggers=[],
        handouts=[{"id": "h_1", "kind": "letter", "title": "泛黄的信", "content": "别下去。"}],
    )
    hero = Character(
        name="伊芙琳", rule_system="coc", is_player=True,
        base_attributes={"力量": 50, "意志": 60},
        skills={"侦查": 60, "心理学": 70},
        system_data={"hp": 11, "max_hp": 11, "mp": 12, "san": 60, "max_san": 99},
    )
    db.add_all([module, hero])
    db.flush()
    session = GameSession(
        module_id=module.id, player_character_id=hero.id, status="active",
        current_scene_id="scene_a", world_state={},
    )
    db.add(session)
    db.commit()
    return session.id, session, module, hero


class _NoLLM:
    """两条路径都不该为这些「状态变更型」指令去调模型；真调了这里会立刻炸出来。"""

    def supports_tools(self) -> bool:
        return True

    async def complete(self, *a, **kw):
        raise AssertionError("状态变更不该触发 LLM 调用")

    async def stream(self, *a, **kw):
        raise AssertionError("状态变更不该触发 LLM 调用")
        yield ""

    async def stream_chat(self, *a, **kw):
        raise AssertionError("状态变更不该触发 LLM 调用")
        yield ""


# 一步 = (工具名, 工具参数, 等价的文本指令)。用例可以有多步：像「解封」这种只有先封上
# 才谈得上解开的动作，单步跑出来两边都是 no-op——那种「空比对」的等价性一文不值。
Step = tuple[str, dict, str]
INTENTS: list[tuple[str, list[Step]]] = [
    ("切换场景", [
        ("scene_change", {"scene_id": "scene_b"}, "[SCENE_CHANGE: scene_id=scene_b]"),
    ]),
    ("设置剧情 flag", [
        ("set_flag", {"flag": "door_opened"}, "[SET_FLAG: flag=door_opened]"),
    ]),
    ("先设后清剧情 flag", [
        ("set_flag", {"flag": "door_opened"}, "[SET_FLAG: flag=door_opened]"),
        ("clear_flag", {"flag": "door_opened"}, "[CLEAR_FLAG: flag=door_opened]"),
    ]),
    ("发放手书", [("handout", {"id": "h_1"}, "[HANDOUT: id=h_1]")]),
    # 多参数一律逗号分隔——parse_tag_kv 只按逗号切（提示词教的也是这个格式）。
    ("封路", [
        ("block_path", {"scene_id": "scene_b", "reason": "塌了"},
         "[BLOCK_PATH: scene_id=scene_b, reason=塌了]"),
    ]),
    ("先封后解", [
        ("block_path", {"scene_id": "scene_b", "reason": "塌了"},
         "[BLOCK_PATH: scene_id=scene_b, reason=塌了]"),
        ("unblock_path", {"scene_id": "scene_b"}, "[UNBLOCK_PATH: scene_id=scene_b]"),
    ]),
    ("建议前往", [
        ("travel_suggest", {"scene_id": "scene_b"}, "[TRAVEL_SUGGEST: scene_id=scene_b]"),
    ]),
]

# 初始快照：用来断言每条用例**确实改动了什么**，杜绝「两边都没干活」的假等价。
_PRISTINE = {
    "events": [], "current_scene_id": "scene_a", "flags": {},
    "blocked_scenes": {}, "handouts_issued": [], "travel_suggested": [],
}


# 暂不做逐字节比对的 state 类工具，各自写清理由——**不是**「懒得测」的收纳箱。
_DEFERRED: dict[str, str] = {
    # 开战/开追要建整套战斗态（先攻序列含 d100 掷骰），两次运行本就不该相同；
    # 等价性由 test_combat_service / test_chase_service 的确定性断言各自覆盖。
    "start_combat": "先攻含掷骰，非确定性",
    "start_chase": "追逐轨含掷骰，非确定性",
    # 扣血要落 dice/system 事件并触发重伤/濒死判定，牵连规则引擎；
    # 已有 test_major_wound / test_first_aid 覆盖，此处重复比对收益低。
    "hp_change": "牵连伤害结算链路，另有专门用例",
    # mark_seen 只写幂等台账、不产事件，两条路径共用同一个 _exec_mark_seen，
    # 没有各自的解析分支可分歧。
    "mark_seen": "两路共用同一执行函数，无分歧面",
}


def _snapshot(db, session_id: str) -> dict:
    """比对口径：事件的 (类型, actor, 内容, 可见性) + 受影响的世界状态。"""
    session = db.get(GameSession, session_id)
    events = (
        db.query(EventLog)
        .filter(EventLog.session_id == session_id)
        .order_by(EventLog.sequence_num)
        .all()
    )
    ws = session.world_state or {}
    return {
        "events": [
            (e.event_type, e.actor_name, e.content, tuple(e.visibility or []))
            for e in events
        ],
        "current_scene_id": session.current_scene_id,
        "flags": ws.get("flags") or {},
        "blocked_scenes": ws.get("blocked_scenes") or {},
        "handouts_issued": ws.get("handouts_issued") or [],
        "travel_suggested": sorted(ws.get("travel_suggested") or []),
    }


async def _run_tool_path(db, steps: list[Step]) -> list[dict]:
    """走工具路径，**每一步后各取一次快照**——只比终态的话，往返型用例（先封后解）
    净效果为零，两条路径就算中途各走各的也照样「等价」。"""
    session_id, session, module, hero = _seed(db)
    result = ["", "", [], [], []]
    executor = kp_tool_loop._build_kp_tool_executor(
        db, session_id, session, module, hero, [], _NoLLM(), result,
    )
    snaps = []
    for i, (tool, args, _text) in enumerate(steps):
        await executor(ToolCall(id=f"c{i}", name=tool, arguments=args))
        db.commit()
        snaps.append(_snapshot(db, session_id))
    return snaps


async def _run_text_path(db, steps: list[Step]) -> list[dict]:
    session_id, session, module, hero = _seed(db)
    snaps = []
    for _tool, _args, text in steps:
        async for _ in kp_tool_loop._process_commands(
            db, session_id, text, module, hero, session, _NoLLM(), teammates=[],
        ):
            pass
        db.commit()
        snaps.append(_snapshot(db, session_id))
    return snaps


@pytest.mark.parametrize("name,steps", INTENTS, ids=[i[0] for i in INTENTS])
@pytest.mark.asyncio
async def test_两条路径落库结果一致(db_factory, name, steps):
    with db_factory() as db_a:
        tool_snap = await _run_tool_path(db_a, steps)
    with db_factory() as db_b:
        text_snap = await _run_text_path(db_b, steps)
    for i, (a, b) in enumerate(zip(tool_snap, text_snap, strict=True), start=1):
        assert a == b, (
            f"「{name}」第 {i} 步后两条路径落库结果不一致"
            f"——工具路径与文本兼容路径必须等价。\n"
            f"  工具路径: {a}\n  文本路径: {b}"
        )


@pytest.mark.parametrize("name,steps", INTENTS, ids=[i[0] for i in INTENTS])
@pytest.mark.asyncio
async def test_每条用例都确实改动了状态(db_factory, name, steps):
    """守住上一条断言的前提：两边都 no-op 时它也会通过，那种等价性毫无意义。"""
    with db_factory() as db:
        snaps = await _run_tool_path(db, steps)
    changed = {k for snap in snaps for k, v in snap.items() if v != _PRISTINE[k]}
    assert changed, f"「{name}」从头到尾什么都没变——这条等价性是空比对"


@pytest.mark.asyncio
async def test_契约清单覆盖了全部状态变更型工具(db_factory):
    """新增改状态的工具却忘了加等价性用例时，在这里失败。

    只盯 ``state`` 类工具（fire-and-continue 的状态变更）——``check`` / ``lookup`` / ``npc``
    三类要么带掷骰随机性、要么要调模型，不适合逐字节比对，各自另有专门用例。
    """
    from app.ai import tools as kp_tools

    state_tools = {spec.name for spec in kp_tools.REGISTRY if spec.kind == "state"}
    assert state_tools, "REGISTRY 里一个 state 类工具都没有——这条断言退化成了空集通过"

    covered = {tool for _, steps in INTENTS for tool, _, _ in steps}
    missing = state_tools - covered - set(_DEFERRED)
    assert not missing, (
        f"这些状态变更型工具还没有双路径等价性用例：{sorted(missing)}。"
        "请在 INTENTS 里补上它与等价文本指令的对照；确实不适合逐字节比对的，"
        "加进 _DEFERRED 并写清理由。"
    )


@pytest.mark.asyncio
async def test_场景切换在两条路径都真的改了会话场景(db_factory):
    """给等价性一个「不是两边都没干活」的锚——否则全 no-op 也能让上面的断言通过。"""
    step = [("scene_change", {"scene_id": "scene_b"}, "[SCENE_CHANGE: scene_id=scene_b]")]
    with db_factory() as db:
        assert (await _run_tool_path(db, step))[-1]["current_scene_id"] == "scene_b"
    with db_factory() as db:
        assert (await _run_text_path(db, step))[-1]["current_scene_id"] == "scene_b"


@pytest.mark.asyncio
async def test_两条路径共享同一套授权与场景校验(db_factory):
    """不连通/不存在的场景，两条路径都必须拒绝——否则文本路径会成为绕过校验的后门。"""
    step = [("scene_change", {"scene_id": "scene_不存在"}, "[SCENE_CHANGE: scene_id=scene_不存在]")]
    with db_factory() as db:
        tool_snap = await _run_tool_path(db, step)
    with db_factory() as db:
        text_snap = await _run_text_path(db, step)
    assert tool_snap[-1]["current_scene_id"] == text_snap[-1]["current_scene_id"] == "scene_a"
    assert tool_snap == text_snap


def test_session_service_仍暴露导航与回合态的同名入口():
    """两条路径都经 session_service 取数；re-export 断了会让其中一条静默走空。"""
    for fn in ("scene_neighbors", "get_participants", "commit_turn"):
        assert callable(getattr(session_service, fn, None)), f"session_service.{fn} 不见了"
