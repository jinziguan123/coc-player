"""轨迹用量的归属与对账：各段记的是段内全部调用之和，独占（嵌套/并发都不重复计），
且各环节之和 = 该次生成落进本局累计的增量。不调 LLM，直接往累加器里报 usage。
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.ai import usage_tracker
from app.models import Base, Character, GameSession, Module, SessionStats  # noqa: F401
from app.models.agent_trace import AgentTraceLog
from app.services import agent_trace


def _call(prompt: int, completion: int, *, reasoning: int = 0, cache_hit: int = 0) -> None:
    """模拟 Provider 拿到一次服务端 usage。"""
    usage_tracker.add({
        "prompt_tokens": prompt, "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "completion_tokens_details": {"reasoning_tokens": reasoning},
        "prompt_cache_hit_tokens": cache_hit,
    })


@pytest.fixture
def TS(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'attr.db'}", connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr("app.database.SessionLocal", factory, raising=False)
    return factory


@pytest.fixture
def sid(TS) -> str:
    db = TS()
    module = Module(title="M", rule_system="coc", npcs=[], scenes=[])
    pc = Character(name="调查员", rule_system="coc", is_player=True)
    db.add_all([module, pc]); db.flush()
    gs = GameSession(module_id=module.id, player_character_id=pc.id, status="active", world_state={})
    db.add(gs); db.commit()
    out = gs.id
    db.close()
    return out


def _rows(TS, sid) -> dict[str, AgentTraceLog]:
    with TS() as db:
        return {r.stage: r for r in db.query(AgentTraceLog).filter_by(session_id=sid).all()}


def test_span_sums_every_call_not_just_the_last(TS, sid):
    """工具循环一段里三次调用：此前取 last_usage 只剩最后一次，现在是三次之和，且带思考/缓存。"""
    async def gen():
        with agent_trace.span("kp", "主叙事（工具循环）"):
            _call(1000, 200, reasoning=50, cache_hit=600)
            _call(1500, 100, cache_hit=1000)
            _call(1800, 300, reasoning=100)

    asyncio.run(usage_tracker.tracked(sid, gen()))
    row = _rows(TS, sid)["主叙事（工具循环）"]
    assert (row.prompt_tokens, row.completion_tokens, row.total_tokens) == (4300, 600, 4900)
    assert row.reasoning_tokens == 150 and row.cache_read_tokens == 1600 and row.calls == 3


def test_nested_and_concurrent_spans_do_not_double_count(TS, sid):
    """NPC 代演嵌在主叙事里、两名队友并发决策：各段独占，合计不超过本局累计。"""
    async def teammate(name: str, n: int):
        with agent_trace.span("team", f"队友决策 · {name}"):
            await asyncio.sleep(0)       # 让两段交错执行
            _call(n, 10)
            await asyncio.sleep(0)
            _call(n, 10)

    async def gen():
        await asyncio.gather(teammate("甲", 100), teammate("乙", 300))
        with agent_trace.span("kp", "主叙事"):
            _call(2000, 500)
            with agent_trace.span("npc", "NPC 代演 · 老板"):
                _call(400, 40)
            _call(2500, 100)

    asyncio.run(usage_tracker.tracked(sid, gen()))
    rows = _rows(TS, sid)
    assert rows["队友决策 · 甲"].total_tokens == 220
    assert rows["队友决策 · 乙"].total_tokens == 620
    assert rows["NPC 代演 · 老板"].total_tokens == 440
    assert rows["主叙事"].total_tokens == 2500 + 2600       # 不含嵌套的 NPC 那 440
    with TS() as db:
        su = db.get(SessionStats, sid).session_usage
    assert sum(r.total_tokens for r in rows.values()) == su["total_tokens"]


def test_by_agent_ledger_reconciles_with_generation_delta(TS, sid):
    """各环节合计 = 该次生成的累计增量；漏埋的调用如实落在 untraced 里。"""
    async def gen(extra_untraced: bool):
        with agent_trace.span("planner", "规划本回合"):
            _call(800, 120)
        with agent_trace.span("kp", "主叙事"):
            _call(3000, 600, cache_hit=1500)
        with agent_trace.span("validator", "终检本回合叙事"):
            _call(900, 30)
            _call(1200, 80)               # 快模型不可用 → 回退叙事模型
        if extra_untraced:
            _call(50, 5)                  # 没挂在任何段上的调用

    asyncio.run(usage_tracker.tracked(sid, gen(False)))
    with TS() as db:
        s = agent_trace.usage_summary(db, sid)
    assert s["untraced"]["total_tokens"] == 0
    assert s["traced"]["total_tokens"] == s["session_total"]["total_tokens"] == 6730
    by = {a["agent"]: a for a in s["agents"]}
    assert by["validator"]["calls"] == 2 and by["validator"]["total_tokens"] == 2210
    assert by["kp"]["cache_read_tokens"] == 1500
    assert s["agents"][0]["agent"] == "kp"     # 按 token 从多到少

    before = s["session_total"]["total_tokens"]
    asyncio.run(usage_tracker.tracked(sid, gen(True)))
    with TS() as db:
        s = agent_trace.usage_summary(db, sid)
    assert s["session_total"]["total_tokens"] - before == 6730 + 55
    assert s["untraced"] == {**usage_tracker._zero(), "prompt_tokens": 50,
                             "completion_tokens": 5, "total_tokens": 55, "calls": 1}


def test_ledger_baseline_excludes_history_before_it_existed(TS, sid):
    """老存档：账本上线前的累计不分环节，对账时先扣掉，否则差额永远是一大截历史。"""
    with TS() as db:
        db.add(SessionStats(session_id=sid, session_usage={"total_tokens": 9000, "calls": 30}))
        db.commit()

    async def gen():
        with agent_trace.span("kp", "主叙事"):
            _call(100, 20)

    asyncio.run(usage_tracker.tracked(sid, gen()))
    with TS() as db:
        s = agent_trace.usage_summary(db, sid)
    assert s["since"]["total_tokens"] == 9000
    assert s["session_total"]["total_tokens"] == 9120
    assert s["untraced"]["total_tokens"] == 0


def test_phase_prefixes_stage(TS, sid):
    async def gen():
        with usage_tracker.phase("投骰"):
            with agent_trace.span("kp", "主叙事"):
                _call(10, 1)

    asyncio.run(usage_tracker.tracked(sid, gen()))
    assert "投骰 · 主叙事" in _rows(TS, sid)


def test_session_scoped_endpoint_persists_and_generation_task_keeps_own_ledger(TS, sid):
    """生成之外的入口（真人 KP 参谋等）也记进本局；从这种入口起的生成任务另起一本账，不被外层吞掉。"""
    spawned: list[asyncio.Task] = []

    async def gen():
        with agent_trace.span("kp", "主叙事"):
            _call(700, 70)

    @usage_tracker.session_scoped
    async def endpoint(session_id: str):
        with agent_trace.span("advisor", "参谋 · 叙事草稿"):
            _call(300, 30)
        spawned.append(asyncio.create_task(usage_tracker.tracked(session_id, gen())))
        return "ok"

    async def main():
        assert await endpoint(session_id=sid) == "ok"
        await spawned[0]

    asyncio.run(main())
    with TS() as db:
        s = agent_trace.usage_summary(db, sid)
    assert s["session_total"]["total_tokens"] == 330 + 770
    assert {a["agent"] for a in s["agents"]} == {"advisor", "kp"}
    assert s["untraced"]["total_tokens"] == 0


def test_span_outcome_on_error_and_explicit_finish_wins(TS, sid):
    async def gen():
        with pytest.raises(RuntimeError):
            with agent_trace.span("npc", "NPC 代演 · 甲"):
                _call(5, 1)
                raise RuntimeError("boom")
        with agent_trace.span("intent", "意图裁决") as trace:
            agent_trace.finish(trace, outcome="conflict")

    asyncio.run(usage_tracker.tracked(sid, gen()))
    rows = _rows(TS, sid)
    assert rows["NPC 代演 · 甲"].outcome == "error" and rows["NPC 代演 · 甲"].total_tokens == 6
    assert rows["意图裁决"].outcome == "conflict" and rows["意图裁决"].calls is None


def test_kp_agent_narrate_opens_its_own_span(TS, sid):
    """非工具路径的主叙事都经 KPAgent.narrate：在那一处记一段，流式里的调用都归它。"""
    from app.ai.agents.kp_agent import KPAgent

    class FakeLLM:
        async def stream(self, messages, temperature=None, max_tokens=None):
            yield "门吱呀一声开了。"
            _call(1200, 80)

    async def gen():
        with usage_tracker.phase("开场"):
            async for _ in KPAgent(FakeLLM()).narrate([{"role": "user", "content": "开场"}]):
                pass

    asyncio.run(usage_tracker.tracked(sid, gen()))
    row = _rows(TS, sid)["开场 · 主叙事"]
    assert row.agent == "kp" and row.total_tokens == 1280 and row.calls == 1
