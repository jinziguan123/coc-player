"""轻量 Agent 调用轨迹：结构化日志，不新增数据库表。

用途是把「一轮总耗时 90s」拆到具体 Agent：谁、哪一阶段、多久、用了多少 token、
是否超时。当前先写 logger（agent_trace|...），供开发/运维 grep；后续若要在 UI 里展示，
再把同一结构落表即可。
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger("agent.trace")


@dataclass
class AgentTrace:
    session_id: str
    agent: str
    stage: str = ""
    started: float = field(default_factory=time.monotonic)
    fields: dict[str, Any] = field(default_factory=dict)
    db: Session | None = None


def start(session_id: str, agent: str, db: Session | None = None, **fields: Any) -> AgentTrace:
    return AgentTrace(session_id=session_id, agent=agent, fields=dict(fields), db=db)


def finish(trace: AgentTrace, *, outcome: str = "ok", provider=None, **fields: Any) -> None:
    payload = {
        "session": trace.session_id,
        "agent": trace.agent,
        "stage": trace.stage,
        "outcome": outcome,
        "duration_ms": round((time.monotonic() - trace.started) * 1000),
        **trace.fields,
        **fields,
    }
    usage = getattr(provider, "last_usage", None) if provider is not None else None
    if isinstance(usage, dict):
        payload["usage"] = {
            key: usage.get(key)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            if usage.get(key) is not None
        }
    logger.info("agent_trace|%s", json.dumps(payload, ensure_ascii=False, default=str))
    _persist(trace, payload)


def _persist(trace: AgentTrace, payload: dict) -> None:
    """把轨迹写进 agent_trace_logs；失败只记日志，绝不影响跑团。"""
    if trace.db is None:
        return
    try:
        from sqlalchemy.orm import sessionmaker

        from app.models.agent_trace import AgentTraceLog

        factory = sessionmaker(bind=trace.db.get_bind())
        with factory() as tdb:
            usage = payload.get("usage") or {}
            tdb.add(AgentTraceLog(
                session_id=trace.session_id,
                agent=trace.agent,
                stage=trace.stage,
                outcome=payload.get("outcome") or "ok",
                duration_ms=int(payload.get("duration_ms") or 0),
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"),
                detail=payload,
            ))
            tdb.commit()
    except Exception:
        logger.exception("agent_trace 落库失败（忽略）: session=%s agent=%s", trace.session_id, trace.agent)


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
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]
