"""检定落地时的门控移动批次结算：消费值快照，产出提交批次，不读写存档或数据库。"""
from collections.abc import Mapping
from dataclasses import dataclass

from app.application.movement_execution import MoveCommand, MovementStateUpdate
from app.application.movement_state import MoveRecord, PendingMove
from app.domain.checks import skill_matches
from app.domain.movement import CheckOutcome, settle_check


@dataclass(frozen=True)
class CheckSettlement:
    """一次检定结果对全部待检定移动的结算。``rollers`` 按条目给出本次真正被结算的人，供日志。"""
    commands: tuple[MoveCommand, ...]
    state: MovementStateUpdate
    rollers: tuple[tuple[str, ...], ...]


def settle_pending_checks(
    pending: tuple[PendingMove, ...], records: tuple[MoveRecord, ...], *,
    skill: str, outcome: CheckOutcome, locations: Mapping[str, str | None],
) -> CheckSettlement | None:
    """逐条按技能匹配结算；同一结果可同时结算多条待检定移动。

    ``locations`` 是写入前的位置快照，只含仍在队伍里的角色；不在其中或已离开出发处的人
    没有通过资格。没有任何条目发生变化返回 None（调用方不提交，重复或无关结果无副作用）。
    结算完的条目移出待检定列表，对应裁定记录同步个人状态与整条状态。
    """
    if not outcome.actor_ids:
        return None
    entries = list(pending)
    updated_records = list(records)
    commands: list[MoveCommand] = []
    rollers: list[tuple[str, ...]] = []
    for index, entry in enumerate(entries):
        if not skill_matches(entry.skill, skill):
            continue
        resolution = settle_check(
            dict(entry.states), outcome, shared=entry.shared,
            eligible_ids=frozenset(
                cid for cid, _status in entry.states
                if cid in locations and locations[cid] == entry.origin
            ),
        )
        if not resolution.changed:
            continue
        entries[index] = entry.with_states(resolution.states)
        rollers.append(resolution.roller_ids)
        if resolution.mover_ids:
            commands.append(MoveCommand(str(entry.target or ""), resolution.mover_ids))
        updated_records = [
            r.with_states(resolution.states) if r.concerns(entry) else r for r in updated_records
        ]
    if not rollers:
        return None
    return CheckSettlement(
        tuple(commands),
        MovementStateUpdate(tuple(updated_records), tuple(e for e in entries if e.waiting_ids)),
        tuple(rollers),
    )
