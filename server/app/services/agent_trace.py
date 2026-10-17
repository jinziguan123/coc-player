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

logger = logging.getLogger("agent.trace")


@dataclass
class AgentTrace:
    session_id: str
    agent: str
    stage: str = ""
    started: float = field(default_factory=time.monotonic)
    fields: dict[str, Any] = field(default_factory=dict)


def start(session_id: str, agent: str, **fields: Any) -> AgentTrace:
    return AgentTrace(session_id=session_id, agent=agent, fields=dict(fields))


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
