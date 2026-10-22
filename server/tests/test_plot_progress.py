"""剧情进度外置 + 剧情线台账 + 压缩后自检。全部用假 LLM，不花 token。

1. 规划器 set_flags 落库：只认模组钩子里的标志，落了之后事件闸门打开、记一条仅 KP 可见的事件；
2. 剧情线台账（决定/承诺/悬念/待办）：摘要差量增删正确，只进 KP 上下文、不下发玩家；
3. 压缩后自检：摘要漏掉的线索 / 人物按原名补记进章节，漏掉的承诺从原文补回台账。
"""

from __future__ import annotations

import asyncio
import copy
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.ai import story_summarizer, turn_planner
from app.ai.context import build_kp_context
from app.api.sessions import _player_world_state
from app.models.base import Base
from app.models.character import Character
from app.models.event_log import EventLog
from app.models.module import Module
from app.models.session import GameSession
from app.models.session_participant import SessionParticipant  # noqa: F401 — 注册建表
from app.services import (
    chat_service,
    entity_state,
    plot_progress,
    plot_threads,
    scene_kind,
    session_service,
    summary_audit,
    turn_effects,
)
from tests.test_scene_events import TOMB_NPCS, TOMB_SCENES, TOMB_TRIGGERS, _pc


def _tomb_module(**kw) -> Module:
    return Module(
        title="陵墓", rule_system="coc", description="", world_setting={},
        scenes=copy.deepcopy(TOMB_SCENES), npcs=copy.deepcopy(TOMB_NPCS),
        clues=[{"id": "clue_diary", "name": "卡纳的日记", "location": "scene_3"}],
        triggers=copy.deepcopy(TOMB_TRIGGERS), handouts=[], truth="", **kw,
    )


@pytest.fixture
def db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


@pytest.fixture(autouse=True)
def _no_illustration(monkeypatch):
    monkeypatch.setattr(turn_effects, "_maybe_scene_illustration", lambda *a, **k: [])


def _seed(db, flags: list[str] | None = None, ws: dict | None = None):
    module = _tomb_module()
    player = Character(name="调查员甲", rule_system="coc")
    db.add_all([module, player])
    db.flush()
    session = GameSession(
        module_id=module.id, player_character_id=player.id, current_scene_id="scene_2",
        status="active", world_state={"flags": {f: True for f in flags or []}, **(ws or {})},
    )
    db.add(session)
    db.commit()
    return session, module


def _plan(set_flags=(), clear_flags=(), reason="") -> turn_planner.TurnPlan:
    return turn_planner.TurnPlan(scene_policy=turn_planner.ScenePolicy(
        set_flags=list(set_flags), clear_flags=list(clear_flags), flag_reason=reason,
    ))


async def _drain(gen):
    return [c async for c in gen]


# ── 1. 规划器 set_flags ─────────────────────────────────────────


def test_合法标志白名单_只认模组钩子():
    module = _tomb_module()
    can_set, can_clear = plot_progress.legal_flags(module)
    # triggers 的 set_flags + 剧情事件的 set_flags
    assert {"monster_awake", "trapped_in_tomb"} <= can_set
    assert can_clear == {"trapped_in_tomb"}
    to_set, to_clear = plot_progress.validate_plan_flags(
        module, {"trapped_in_tomb"},
        ["monster_awake", "boss_defeated", "trapped_in_tomb"], ["trapped_in_tomb", "made_up"],
    )
    assert to_set == ["monster_awake"]       # 编造的丢弃，已激活的不重复置
    assert to_clear == ["trapped_in_tomb"]   # 只清模组写明可清、且当前激活的


def test_规划器看得到未触发钩子_已触发的不再给():
    module = _tomb_module()
    hooks = plot_progress.pending_hooks(module, set())
    ids = {h["id"] for h in hooks}
    assert {"trig_2", "scene_6", "scene_0"} <= ids
    trig = next(h for h in hooks if h["id"] == "trig_2")
    assert trig["when"].startswith("经过约30-40分钟") and trig["set_flags"] == ["monster_awake"]

    later = {h["id"] for h in plot_progress.pending_hooks(module, {"monster_awake"})}
    assert "trig_2" not in later and "scene_6" not in later

    session = GameSession(module_id="m", status="active", current_scene_id="scene_2",
                          world_state={"flags": {}})
    msgs = turn_planner.build_turn_plan_messages(session, module, _pc(), [])
    prompt = msgs[1]["content"]
    assert '"plot_hooks"' in prompt and "flag_reason" in prompt


def test_清洗计划_编造的标志不会进KP的裁定计划():
    module = _tomb_module()
    plan = _plan(["boss_defeated"], reason="瞎编的")
    plot_progress.sanitize_plan_flags(plan, module, set())
    assert plan.scene_policy.set_flags == [] and plan.scene_policy.flag_reason == ""
    assert "【剧情推进" not in turn_planner.build_turn_plan_message(plan)["content"]

    plan = _plan(["monster_awake"], reason="调查员已探索完前室与耳室")
    plot_progress.sanitize_plan_flags(plan, module, set())
    body = turn_planner.build_turn_plan_message(plan)["content"]
    assert "【剧情推进" in body and "调查员已探索完前室与耳室" in body


def test_条件满足的钩子落标志_闸门打开_记KP专属事件(db):
    session, module = _seed(db, flags=["trapped_in_tomb"])
    assert "npc_1" in scene_kind.dormant_npcs(module.scenes, entity_state.active_flags(session))
    plan = _plan(["monster_awake", "boss_defeated"], ["trapped_in_tomb"],
                 reason="调查员已探索完前室和耳室")
    asyncio.run(_drain(plot_progress.apply_planned_flags(db, session.id, session, module, plan)))

    db.refresh(session)
    flags = entity_state.active_flags(session)
    assert flags == {"monster_awake"}                       # 置上 + 清掉；编造的没进来
    assert scene_kind.dormant_npcs(module.scenes, flags) == {}   # 可怖之物不再蛰伏

    rows = db.query(EventLog).filter(EventLog.session_id == session.id).all()
    notes = [e for e in rows if (e.metadata_ or {}).get("kind") == plot_progress.PLOT_PROGRESS_KIND]
    assert len(notes) == 1
    assert session_service.is_kp_only_event(notes[0])         # 玩家永远不可见
    assert "可怖之物" in notes[0].content and "调查员已探索完前室和耳室" in notes[0].content
    assert "boss_defeated" not in notes[0].content
    # 玩家视角的事件流（前端历史 / 重连分页）里没有它
    page, _ = session_service.get_latest_events(db, session.id)
    assert all(e.id != notes[0].id for e in page)

    # 幂等：同一计划再跑一次不重复落事件
    asyncio.run(_drain(plot_progress.apply_planned_flags(db, session.id, session, module, plan)))
    rows = db.query(EventLog).filter(EventLog.session_id == session.id).all()
    assert sum((e.metadata_ or {}).get("kind") == "plot_progress" for e in rows) == 1


def test_全是非法标志时什么都不做(db):
    session, module = _seed(db)
    asyncio.run(_drain(plot_progress.apply_planned_flags(
        db, session.id, session, module, _plan(["boss_defeated"]),
    )))
    db.refresh(session)
    assert entity_state.active_flags(session) == set()
    assert db.query(EventLog).filter(EventLog.session_id == session.id).count() == 0


# ── 2. 剧情线台账 ─────────────────────────────────────────────


def test_台账差量_增删正确_非法id忽略():
    ws, tid = plot_threads.add_thread({}, "promise", "答应酒保天亮前带回怀表", 10)
    assert tid == "t1"
    ws = plot_threads.apply_thread_delta(ws, {
        "new": [
            {"kind": "mystery", "text": "石棺上的祭祀场景指向谁？"},
            {"kind": "mystery", "text": "石棺上的祭祀场景指向谁？"},  # 同文去重
            {"kind": "奇怪类型", "text": "回营地找补给"},             # 未知类型 → 待办
            "  ",                                                    # 空的丢弃
        ],
        "resolved": [{"id": "t1", "how": "把怀表交还酒保"}, {"id": "t99"}],
    }, from_seq=20, to_seq=30)
    items = plot_threads.threads(ws)
    assert items["t1"]["status"] == "resolved" and items["t1"]["resolution"] == "把怀表交还酒保"
    assert items["t1"]["resolved_seq"] == 30
    opened = [t for _, t in plot_threads.open_threads(ws)]
    assert [(t["kind"], t["text"]) for t in opened] == [
        ("mystery", "石棺上的祭祀场景指向谁？"), ("todo", "回营地找补给"),
    ]
    assert all(t["since_seq"] == 20 for t in opened)
    # 坏形状不抛异常
    assert plot_threads.apply_thread_delta(ws, "胡言乱语", 1, 2) == ws
    assert plot_threads.apply_thread_delta(ws, {"new": "x", "resolved": 3}, 1, 2) == ws


def test_台账单次新增有上限():
    ws = plot_threads.apply_thread_delta({}, {
        "new": [{"kind": "todo", "text": f"待办{i}"} for i in range(30)],
    }, 1, 2)
    assert len(plot_threads.threads(ws)) == plot_threads.MAX_NEW_PER_DELTA


def test_台账只进KP上下文_不下发玩家():
    ws, _ = plot_threads.add_thread({}, "mystery", "管家昨夜去了哪里", 5)
    module = _tomb_module()
    session = GameSession(module_id="m", status="active", current_scene_id="scene_2",
                          world_state={"flags": {}, **ws})
    ev = type("E", (), {
        "event_type": "narration", "visibility": ["all"], "content": "前室一片寂静。",
        "actor_name": "KP", "actor_id": None, "metadata_": {}, "sequence_num": 1,
    })()
    text = "\n".join(str(m.get("content") or "") for m in build_kp_context(
        session, module, _pc(), [ev],
    ))
    assert "【未了结的剧情线】" in text and "[悬念] 管家昨夜去了哪里" in text
    # 玩家视角的 world_state 白名单里没有台账与自检记录
    player_ws = _player_world_state({**ws, "summary_audit": {"missing_clues": ["x"]}})
    assert "plot_threads" not in player_ws and "summary_audit" not in player_ws


def test_摘要提示词带未了结剧情线_返回thread_updates():
    class _LLM:
        async def complete(self, messages, **kw):
            self.messages = messages
            return json.dumps({
                "summary": "梗概。", "npc_updates": {}, "clue_notes": {},
                "thread_updates": {"new": [{"kind": "promise", "text": "答应卡纳带他回家"}]},
            }, ensure_ascii=False)

    llm = _LLM()
    ev = type("E", (), {"event_type": "narration", "content": "一些事。", "actor_name": "KP"})()
    got = asyncio.run(story_summarizer.summarize_and_extract(
        llm, "", [ev], "", threads_brief="- t1 [悬念] 石门为何会自己关上",
    ))
    assert got is not None and len(got) == 5
    assert got[4] == {"new": [{"kind": "promise", "text": "答应卡纳带他回家"}]}
    prompt = llm.messages[1]["content"]
    assert "thread_updates" in prompt and "t1 [悬念] 石门为何会自己关上" in prompt


# ── 3. 压缩后自检 ─────────────────────────────────────────────


def _ev(seq, etype, content, actor=""):
    return type("E", (), {
        "event_type": etype, "content": content, "actor_name": actor, "sequence_num": seq,
    })()


def _ws_with_chapter(text, lo=1, hi=10, **extra):
    return {
        "story_chapters": [{"text": text, "from_seq": lo, "to_seq": hi}],
        "story_summary": text, **extra,
    }


def test_自检_漏掉的线索与人物按原名补记():
    module = _tomb_module()
    ws = _ws_with_chapter(
        "调查员在甬道里摸索，进入前室。",
        clue_ledger={"clue_diary": {"status": "known", "seq": 4}},
        npc_memory={"npc_2": {"interactions": [{"seq": 6, "summary": "检视遗骸"}]}},
    )
    ws, report = summary_audit.audit_chapter(ws, module, set())
    assert report["missing_clues"] == ["卡纳的日记"]
    assert report["missing_npcs"] == ["萨沙·卡纳"]
    chapter = ws["story_chapters"][-1]["text"]
    assert "「卡纳的日记」" in chapter and "「萨沙·卡纳」" in chapter
    assert ws["story_summary"].endswith(chapter.splitlines()[-1])     # 拼接快照同步
    assert ws["summary_audit"]["missing_clues"] == ["卡纳的日记"]


def test_自检_已提到的不补_区间外的不管():
    module = _tomb_module()
    ws = _ws_with_chapter(
        "调查员在耳室见到卡纳的遗骸，翻出了卡纳的日记。", lo=1, hi=10,
        clue_ledger={"clue_diary": {"status": "known", "seq": 4}},
        npc_memory={"npc_1": {"interactions": [{"seq": 50, "summary": "后来的事"}]}},
    )
    ws2, report = summary_audit.audit_chapter(ws, module, set())
    assert report["missing_clues"] == [] and report["missing_npcs"] == []
    assert ws2["story_chapters"][-1]["text"] == ws["story_chapters"][-1]["text"]


def test_自检_被摘要丢掉的承诺从原文补回台账():
    module = _tomb_module()
    events = [
        _ev(3, "dialogue", "我向你保证，天亮之前一定把怀表还给你。", "调查员甲"),
        _ev(4, "dialogue", "我答应你，绝不把卡纳的事告诉警察。", "调查员甲"),
        _ev(5, "narration", "墙上的火把噼啪作响。", "KP"),
    ]
    # 第二条承诺摘要里写到了（有实质重合），第一条被概括掉了
    ws = _ws_with_chapter("调查员许诺绝不把卡纳的事告诉警察。")
    ws, report = summary_audit.audit_chapter(ws, module, set(), events=events)
    assert len(report["added_threads"]) == 1
    added = [t for _, t in plot_threads.open_threads(ws)]
    assert added[0]["kind"] == "promise" and added[0]["source"] == "audit"
    assert "天亮之前一定把怀表还给你" in added[0]["text"]
    # 再跑一次不重复补
    ws, report = summary_audit.audit_chapter(ws, module, set(), events=events)
    assert report["added_threads"] == [] and len(plot_threads.threads(ws)) == 1


def test_自检_新触发未提及的剧情进度只记录不写进章节():
    module = _tomb_module()
    ws = _ws_with_chapter("调查员在前室翻找陪葬品。")
    ws, report = summary_audit.audit_chapter(ws, module, {"monster_awake"})
    assert "可怖之物苏醒" in report["hooks_unmentioned"]
    assert "苏醒" not in ws["story_chapters"][-1]["text"]   # 钩子标题可能是守秘人用语，不进章节
    # 已在上次自检记过的进度不再重复报
    _, report = summary_audit.audit_chapter(ws, module, {"monster_awake"})
    assert report["hooks_unmentioned"] == []


def test_滚动摘要端到端_摘要丢了线索和承诺也能补回(db, monkeypatch):
    session, module = _seed(db, ws={
        "npc_memory": {}, "clue_ledger": {},
    })
    filler = "火光在甬道里摇晃，墙上的影子跟着起伏，脚步声在石壁间来回撞。" * 13
    for i in range(1, 51):
        content = f"第{i}段。{filler}"
        if i == 3:
            content = "我向萨沙保证，天亮之前一定把他的遗骨带出陵墓。" + filler
        session_service.add_event(db, session.id, "narration", content, actor_name="KP")
    ws = dict(session.world_state)
    ws["clue_ledger"] = {"clue_diary": {"status": "known", "seq": 2}}
    session.world_state = ws
    db.commit()

    class _LLM:
        async def complete(self, messages, **kw):
            # 摘要把线索和承诺都概括掉了，thread_updates 也漏了
            return json.dumps({
                "summary": "调查员在陵墓里四处摸索。", "npc_updates": {}, "clue_notes": {},
                "thread_updates": {"new": [], "resolved": []},
            }, ensure_ascii=False)

    asyncio.run(chat_service._maybe_roll_story_summary(db, session.id, _LLM()))
    db.refresh(session)
    ws = session.world_state
    assert ws["story_summary_seq"] > 0
    chapter = ws["story_chapters"][-1]["text"]
    assert "「卡纳的日记」" in chapter
    texts = [t["text"] for _, t in plot_threads.open_threads(ws)]
    assert any("天亮之前一定把他的遗骨带出陵墓" in t for t in texts)
    assert ws["summary_audit"]["missing_clues"] == ["卡纳的日记"]
