"""队友动作意图、语义冲突与依赖排序。

TeamAgent 的决策从自由文本升级成带 target/verb/depends_on 的意图：
- 同一对象上的互斥动作（open vs block、lock vs open、take vs give）只保留先提交者；
- 依赖别人先完成的动作按 depends_on 拓扑排序；
- 依赖的动作被冲突裁掉时，依赖者也一并丢弃，避免“等一个不会发生的结果”。
没有 target/verb 的旧决策仍走同目标保守冲突。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from app.services import intent_ledger

logger = logging.getLogger(__name__)

_SCOPED_ACTIONS = frozenset({"act", "assist", "check"})

#: 同一 target 上的语义互斥。值里的动词不能与该键同时成立。
_OPPOSITES: dict[str, frozenset[str]] = {
    "open": frozenset({"block", "lock", "close"}),
    "block": frozenset({"open", "enter"}),
    "close": frozenset({"open"}),
    "lock": frozenset({"open", "unlock"}),
    "unlock": frozenset({"lock"}),
    "take": frozenset({"give"}),
    "give": frozenset({"take"}),
    "attack": frozenset({"protect"}),
    "protect": frozenset({"attack"}),
    "enter": frozenset({"block", "leave"}),
    "leave": frozenset({"enter"}),
}

_VERB_ALIASES = {
    "推开": "open", "打开": "open", "开": "open",
    "拦住": "block", "阻止": "block", "阻挡": "block", "拦": "block",
    "锁上": "lock", "反锁": "lock", "锁": "lock",
    "解锁": "unlock",
    "拿走": "take", "抢走": "take", "夺走": "take", "取走": "take",
    "交给": "give", "递给": "give", "给出": "give",
    "攻击": "attack", "袭击": "attack",
    "保护": "protect", "掩护": "protect",
    "进入": "enter", "进去": "enter",
    "离开": "leave", "出去": "leave",
    "查看": "observe", "观察": "observe", "搜查": "search",
}


@dataclass(frozen=True)
class TeamIntent:
    actor_id: str
    actor_name: str
    action: str
    content: str
    target: str
    verb: str
    depends_on: tuple[str, ...]
    observes_seq: int
    target_version: int = 0


def _as_dep_list(value) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        parts = value.replace("，", ",").replace("、", ",").split(",")
        return tuple(p.strip() for p in parts if p.strip())
    if isinstance(value, (list, tuple, set)):
        return tuple(str(v).strip() for v in value if str(v).strip())
    return (str(value).strip(),)


def _norm_verb(value) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    return _VERB_ALIASES.get(raw, raw)


def from_decision(character, decision: dict, observes_seq: int, world_state=None) -> TeamIntent:
    target = str(decision.get("target") or "").strip()
    return TeamIntent(
        actor_id=character.id,
        actor_name=character.name,
        action=str(decision.get("action") or ""),
        content=str(decision.get("content") or ""),
        target=target,
        verb=_norm_verb(decision.get("verb")),
        depends_on=_as_dep_list(decision.get("depends_on")),
        observes_seq=observes_seq,
        target_version=intent_ledger.version(world_state or {}, target),
    )


def _verbs_conflict(left: str, right: str) -> bool:
    if not left or not right:
        return True  # 旧决策没有语义动词：同目标按保守冲突处理
    if left == right:
        return True
    return right in _OPPOSITES.get(left, frozenset())


def _filter_conflicts(intents):
    accepted = []
    claimed: dict[str, TeamIntent] = {}
    for item in intents:
        _character, _decision, intent = item
        if intent.action not in _SCOPED_ACTIONS or not intent.target:
            accepted.append(item)
            continue
        previous = claimed.get(intent.target)
        if previous is not None and _verbs_conflict(previous.verb, intent.verb):
            logger.info(
                "队友意图冲突：target=%s %s.%s 与 %s.%s 互斥，丢弃后者",
                intent.target, previous.actor_name, previous.verb,
                intent.actor_name, intent.verb,
            )
            continue
        claimed[intent.target] = intent
        accepted.append(item)
    return accepted


def _order_dependencies(accepted, committed_actors: set[str]):
    by_id = {item[2].actor_id: item for item in accepted}
    by_name = {item[2].actor_name: item for item in accepted}
    state: dict[str, int] = {}
    ordered = []

    def visit(item) -> bool:
        actor_id = item[2].actor_id
        status = state.get(actor_id, 0)
        if status == 2:
            return True
        if status == 1:
            logger.info("队友意图依赖成环：%s，丢弃该分支", item[2].actor_name)
            return False
        state[actor_id] = 1
        for dep in item[2].depends_on:
            target = by_name.get(dep) or by_id.get(dep)
            if target is None:
                if dep in committed_actors:
                    continue
                logger.info(
                    "队友意图依赖缺失：%s 依赖 %s，但对方本轮没有可提交动作，丢弃",
                    item[2].actor_name, dep,
                )
                return False
            if not visit(target):
                return False
        state[actor_id] = 2
        ordered.append(item)
        return True

    for item in accepted:
        if not visit(item):
            state[item[2].actor_id] = 2
    return ordered


def resolve_team_intents(
    intents: list[tuple[object, dict, TeamIntent]],
    committed_actors: set[str] | None = None,
    world_state: dict | None = None,
):
    """同目标互斥 -> depends_on 拓扑 -> 对象状态前置检查；返回可提交顺序。"""
    accepted = _filter_conflicts(intents)
    committed = set(committed_actors or ())
    ordered = _order_dependencies(accepted, committed)
    if world_state is not None:
        allowed = []
        for item in ordered:
            intent = item[2]
            if intent.target and not intent_ledger.allows(world_state, intent.target, intent.verb):
                logger.info(
                    "队友意图前置不满足：%s 对 %s 执行 %s，对象当前状态=%s，丢弃",
                    intent.actor_name, intent.target, intent.verb,
                    intent_ledger.state(world_state, intent.target),
                )
                continue
            allowed.append(item)
        ordered = allowed
    if len(ordered) < len(intents):
        logger.info(
            "队友意图裁决：%d 条提交 / %d 条原始（冲突或依赖缺失）",
            len(ordered), len(intents),
        )
    return ordered
