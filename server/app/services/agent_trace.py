"""Agent 调用轨迹：每段过程花了多久、多少 token，落 agent_trace_logs 供 KP 面板查看。

用途是把「一轮总耗时 90s / 本局消耗 300k」拆到具体环节：谁、哪一阶段、多久、几次调用、
入/出/思考/缓存各多少、是否超时。同时写一行结构化日志（agent_trace|...）供 grep。

用量口径：start 开一段**独占**记账（usage_tracker.open_span），finish 收段——记的是这段
过程里全部 LLM 调用的合计，而不是 provider.last_usage（那只剩最后一次，工具循环、校验
重写都会漏）。嵌套的内层段不算进外层，并发的段互不串账，所以各段之和能与本局累计对上。
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.ai import usage_tracker

logger = logging.getLogger("agent.trace")

_USAGE_KEYS = (
    "prompt_tokens", "completion_tokens", "reasoning_tokens", "total_tokens",
    "cache_read_tokens", "cache_write_tokens", "calls",
)


@dataclass
class AgentTrace:
    session_id: str | None
    agent: str
    stage: str = ""
    started: float = field(default_factory=time.monotonic)
    fields: dict[str, Any] = field(default_factory=dict)
    db: Session | None = None
    usage: dict = field(default_factory=dict)
    token: contextvars.Token | None = None
    finished: bool = False


def start(
    session_id: str | None, agent: str, stage: str, db: Session | None = None, **fields: Any,
) -> AgentTrace:
    """开一段轨迹。**stage 是必填的**：此前它带默认空串，于是六个调用点一个都没传，
    KP 面板那一列常年是一排「—」（实测全表 14 条、零条有值）。必填之后，谁忘了写会当场
    报 TypeError，而不是又留一列空数据等人去发现。

    stage 写的是「这一步在做什么」，尽量带上当事人（哪个队友、哪个 NPC）——面板上那一格
    是排查看「谁慢」时最先读的东西。所在的顶层生成路径（开场/投骰/重新生成…）会自动拼在前面。

    session_id 传 None 时取当前记账范围的局（usage_tracker.scope 设定）；都没有就只写日志。
    """
    phase = usage_tracker.current_phase()
    if phase and not stage.startswith(phase):
        stage = f"{phase} · {stage}"
    usage, token = usage_tracker.open_span()
    return AgentTrace(
        session_id=session_id or usage_tracker.current_session(),
        agent=agent, stage=stage[:64], fields=dict(fields), db=db, usage=usage, token=token,
    )


def finish(trace: AgentTrace, *, outcome: str = "ok", **fields: Any) -> None:
    """收段并落库。重复调用只认第一次（span() 兜底收尾时不会把调用点给的结果覆盖掉）。"""
    if trace.finished:
        return
    trace.finished = True
    if trace.token is not None:
        usage_tracker.close_span(trace.usage, trace.token, trace.agent)
    payload = {
        "session": trace.session_id,
        "agent": trace.agent,
        "stage": trace.stage,
        "outcome": outcome,
        "duration_ms": round((time.monotonic() - trace.started) * 1000),
        **trace.fields,
        **fields,
    }
    if trace.usage.get("calls"):
        payload["usage"] = {k: int(trace.usage.get(k) or 0) for k in _USAGE_KEYS}
    logger.info("agent_trace|%s", json.dumps(payload, ensure_ascii=False, default=str))
    _persist(trace, payload)


@contextlib.contextmanager
def span(agent: str, stage: str, *, session_id: str | None = None, db: Session | None = None,
         **fields: Any):
    """start/finish 的上下文管理器写法：异常按类型记 outcome（取消/超时/出错）后原样上抛。

    用在异步生成器里也成立：生成器在消费方的上下文里运行，段在消费方的任务里开、收。
    调用点想记别的结果，可在块内自行 ``finish(trace, outcome=...)``，这里不会覆盖。
    """
    trace = start(session_id, agent, stage, db=db, **fields)
    try:
        yield trace
    except (asyncio.CancelledError, GeneratorExit):
        finish(trace, outcome="cancelled")
        raise
    except (asyncio.TimeoutError, TimeoutError):
        finish(trace, outcome="timeout")
        raise
    except BaseException:
        finish(trace, outcome="error")
        raise
    else:
        finish(trace)


def _persist(trace: AgentTrace, payload: dict) -> None:
    """把轨迹写进 agent_trace_logs；失败只记日志，绝不影响跑团。

    没给 db 时用全局 SessionLocal——底层模块（终检、去口癖、摘要）手里没有会话。
    """
    if not trace.session_id:
        return
    try:
        from sqlalchemy.orm import sessionmaker

        from app.models.agent_trace import AgentTraceLog

        if trace.db is not None:
            factory = sessionmaker(bind=trace.db.get_bind())
        else:
            from app.database import SessionLocal as factory
        with factory() as tdb:
            usage = payload.get("usage") or {}
            tdb.add(AgentTraceLog(
                session_id=trace.session_id,
                agent=trace.agent,
                stage=trace.stage,
                outcome=payload.get("outcome") or "ok",
                duration_ms=int(payload.get("duration_ms") or 0),
                **{k: usage.get(k) for k in _USAGE_KEYS},
                detail=payload,
            ))
            tdb.commit()
            # 轻量保留策略：每个会话最多 800 条；轨迹只是诊断数据，不需要永久历史。
            # 按环节的累计另记在 session_stats.usage_by_agent，不受这里裁剪影响。
            if tdb.query(AgentTraceLog).filter(
                AgentTraceLog.session_id == trace.session_id,
            ).count() > 800:
                prune_for_session(tdb, trace.session_id, keep=800)
    except Exception:
        logger.exception("agent_trace 落库失败（忽略）: session=%s agent=%s", trace.session_id, trace.agent)


def prune_for_session(db, session_id: str, keep: int = 800) -> int:
    """只保留最近 keep 条，避免轨迹表无限增长。返回删除条数。"""
    from app.models.agent_trace import AgentTraceLog

    keep = max(1, int(keep))
    q = db.query(AgentTraceLog).filter(AgentTraceLog.session_id == session_id)
    total = q.count()
    if total <= keep:
        return 0
    old_ids = [
        row.id for row in q.order_by(AgentTraceLog.created_at.asc()).limit(total - keep).all()
    ]
    if not old_ids:
        return 0
    db.query(AgentTraceLog).filter(AgentTraceLog.id.in_(old_ids)).delete(
        synchronize_session=False,
    )
    db.commit()
    return len(old_ids)


def list_for_session(db, session_id: str, limit: int = 100) -> list[dict]:
    """KP 面板用：最近若干条 Agent 调用摘要。"""
    from app.models.agent_trace import AgentTraceLog

    rows = (
        db.query(AgentTraceLog)
        .filter(AgentTraceLog.session_id == session_id)
        .order_by(AgentTraceLog.created_at.desc())
        .limit(max(1, min(int(limit), 500)))
        .all()
    )
    return [
        {
            "id": row.id,
            "agent": row.agent,
            "stage": row.stage,
            "outcome": row.outcome,
            "duration_ms": row.duration_ms,
            "prompt_tokens": row.prompt_tokens,
            "completion_tokens": row.completion_tokens,
            "total_tokens": row.total_tokens,
            "reasoning_tokens": row.reasoning_tokens,
            "cache_read_tokens": row.cache_read_tokens,
            "cache_write_tokens": row.cache_write_tokens,
            "calls": row.calls,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]


def usage_summary(db, session_id: str) -> dict:
    """面板顶部的对账：按环节的累计用量（不受轨迹表裁剪影响）与本局累计的差额。

    ``untraced`` = （本局累计 − 账本启用时的基线）− 各环节之和，即没挂在任何一段上的调用。
    理论上接近 0；明显偏大说明还有调用点没埋轨迹。
    """
    from app.services import session_stats

    stats = session_stats.get(db, session_id)
    total = usage_tracker.accumulate(None, (stats.session_usage if stats else None) or {})
    ledger = (stats.usage_by_agent if stats else None) or {}
    since = usage_tracker.accumulate(None, ledger.get("since") or {})
    agents = {
        name: usage_tracker.accumulate(None, u)
        for name, u in (ledger.get("agents") or {}).items()
    }
    traced = usage_tracker._zero()
    for u in agents.values():
        traced = usage_tracker.accumulate(traced, u)
    in_scope = {k: total[k] - since[k] for k in total}
    return {
        "agents": [
            {"agent": name, **u}
            for name, u in sorted(agents.items(), key=lambda kv: -kv[1]["total_tokens"])
        ],
        "session_total": total,
        "since": since,
        "traced": traced,
        "untraced": {k: in_scope[k] - traced[k] for k in total},
    }
