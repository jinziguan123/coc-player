"""移动业务类型与检定状态转换；仅接收值，不读写存档或发送消息。"""
from dataclasses import dataclass
from collections.abc import Mapping
from typing import Literal


@dataclass(frozen=True)
class MovementIntent:
    actors: tuple[str, ...] = ()
    stance: str = "act"
    exit: str = ""
    target: str = ""
    solo: bool = False
    blocked: str = ""
    check_skill: str = ""
    shared_check: bool = False


@dataclass(frozen=True)
class PassageDecision:
    status: Literal["unreachable", "blocked", "pending_check", "moved"]
    reason: str = ""
    skill: str = ""


def assess_passage(
    intent: MovementIntent, *, reachable: bool, legacy_gate: str = "",
) -> PassageDecision:
    """目的地已明确且不是原处时，按通行资格、通道阻碍、检定顺序裁定。

    reachable 由导航提供，已包含借道限制；终点本身的 blocked_scenes 标记不是否决依据。
    显式技能优先于旧门控；受阻时不创建待检定移动。
    """
    if not reachable:
        return PassageDecision("unreachable")
    blocked = (intent.blocked or "").strip()
    if blocked:
        return PassageDecision("blocked", reason=blocked)
    skill = (intent.check_skill or "").strip() or legacy_gate
    return PassageDecision("pending_check", skill=skill) if skill else PassageDecision("moved")


@dataclass(frozen=True)
class CheckOutcome:
    actor_ids: frozenset[str]
    succeeded: bool


@dataclass(frozen=True)
class MovementResolution:
    states: tuple[tuple[str, str], ...]
    mover_ids: tuple[str, ...]
    roller_ids: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return bool(self.roller_ids)


def rollup(states: Mapping[str, str]) -> str:
    values = set(states.values())
    if values & {"pending", "tried"}:
        return "pending_check"
    if values == {"moved"}:
        return "moved"
    if values == {"failed"}:
        return "failed"
    return "partial"


def settle_check(
    states: Mapping[str, str], outcome: CheckOutcome, *,
    shared: bool, eligible_ids: frozenset[str],
) -> MovementResolution:
    """个人检定按人结算；共同障碍解除后，先前尝试失败者也可通过。

    eligible_ids 由用例提供：仍存在且仍位于出发处的角色。输入不修改；重复或无关检定无效果。
    返回角色顺序沿用提案顺序，使同一状态及结果产生稳定的执行顺序。
    """
    updated = dict(states)
    rollers = tuple(cid for cid, status in states.items()
                    if cid in outcome.actor_ids and status == "pending")
    movers: tuple[str, ...] = ()
    if rollers and outcome.succeeded:
        winners = tuple(cid for cid, status in states.items() if status in ("pending", "tried")) \
            if shared else rollers
        movers = tuple(cid for cid in winners if cid in eligible_ids)
        for cid in winners:
            updated[cid] = "moved" if cid in eligible_ids else "failed"
    elif rollers:
        for cid in rollers:
            updated[cid] = "tried" if shared else "failed"
        if shared and "pending" not in updated.values():
            updated = {cid: "failed" if status == "tried" else status
                       for cid, status in updated.items()}
    return MovementResolution(tuple(updated.items()), movers, rollers)
