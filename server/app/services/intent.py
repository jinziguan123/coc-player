"""队友动作意图与确定性冲突裁决。

自然语言台词不是结构化意图；这里只把决策里已有的 target/action 提炼出来，处理
「两个队友同时改同一个对象」这种确定性冲突。语义级 open/block 仍需要后续给
TeamAgent 增加 verb 字段；当前先保证同一 target 的非 travel 动作不会同时落库。
"""
from __future__ import annotations

from dataclasses import dataclass

_SCOPED_ACTIONS = frozenset({"act", "assist", "check"})


@dataclass(frozen=True)
class TeamIntent:
    actor_id: str
    actor_name: str
    action: str
    content: str
    target: str
    observes_seq: int


def from_decision(character, decision: dict, observes_seq: int) -> TeamIntent:
    return TeamIntent(
        actor_id=character.id,
        actor_name=character.name,
        action=str(decision.get("action") or ""),
        content=str(decision.get("content") or ""),
        target=str(decision.get("target") or "").strip(),
        observes_seq=observes_seq,
    )


def resolve_team_intents(intents: list[tuple[object, dict, TeamIntent]]):
    """返回按顺序可提交的 (character, decision, intent)；同目标冲突的后到者被丢弃。

    只拦非 travel 的 act/assist/check：它们会修改同一个对象。travel 同目的地可以结伴，
    speak/silent 不涉及资源写，保持完成即提交。
    """
    accepted = []
    claimed: dict[str, str] = {}
    for character, decision, intent in intents:
        if intent.action in _SCOPED_ACTIONS and intent.target:
            owner = claimed.get(intent.target)
            if owner is not None:
                continue
            claimed[intent.target] = intent.actor_id
        accepted.append((character, decision, intent))
    return accepted
