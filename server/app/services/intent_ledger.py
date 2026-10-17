"""动作对象状态账本：给 target 一个跨回合版本与当前状态。

只服务确定性意图裁决，不替代世界模型：
- version 记录同一 target 被改过几次，用于发现陈旧意图；
- state 只记少量可判定状态（locked/open/closed/blocked/taken），
  让下一轮的 open 遇到 locked、enter 遇到 blocked 时能被拦下。
"""
from __future__ import annotations

from copy import deepcopy

_LEDGER_KEY = "intent_ledger"

_STATE_BY_VERB = {
    "open": "open",
    "close": "closed",
    "lock": "locked",
    "unlock": "open",
    "block": "blocked",
    "take": "taken",
    "give": "given",
}


def _key(target: str) -> str:
    return " ".join(str(target or "").strip().lower().split())


def _ledger(world_state: dict | None) -> dict:
    raw = (world_state or {}).get(_LEDGER_KEY)
    return raw if isinstance(raw, dict) else {}


def version(world_state: dict | None, target: str) -> int:
    return int((_ledger(world_state).get(_key(target)) or {}).get("version") or 0)


def state(world_state: dict | None, target: str) -> str:
    return str((_ledger(world_state).get(_key(target)) or {}).get("state") or "")


def allows(world_state: dict | None, target: str, verb: str) -> bool:
    """按对象当前状态判断动作是否可成立；未知 target/verb 一律放行。"""
    if not target or not verb:
        return True
    current = state(world_state, target)
    if current == "locked" and verb in ("open", "enter"):
        return False
    if current == "blocked" and verb == "enter":
        return False
    if current == "closed" and verb == "enter":
        return False
    if current == "taken" and verb == "take":
        return False
    return True


def record(world_state: dict, target: str, actor_id: str, verb: str, seq: int) -> dict:
    """返回新的 world_state；按 target 单调递增 version，并合并少量状态。"""
    ws = deepcopy(dict(world_state or {}))
    key = _key(target)
    if not key:
        return ws
    ledger = dict(ws.get(_LEDGER_KEY) or {})
    entry = dict(ledger.get(key) or {})
    entry["version"] = int(entry.get("version") or 0) + 1
    entry["actor_id"] = actor_id
    entry["verb"] = verb
    entry["seq"] = int(seq or 0)
    new_state = _STATE_BY_VERB.get(verb)
    if new_state:
        entry["state"] = new_state
    ledger[key] = entry
    ws[_LEDGER_KEY] = ledger
    return ws
