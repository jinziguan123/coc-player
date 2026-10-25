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


def checks_to_issue(
    pending: tuple[PendingMove, ...], *, party_ids: frozenset[str], issued_ids: frozenset[str],
) -> tuple[tuple[tuple[str, ...], str], ...]:
    """暂存了门控移动、叙事后却还没挂出或掷出对应检定的人 → ((这些人, 技能), ...)。

    按人看：一人的检定已挂出或已掷，不代表其他人的也有了。共同障碍只需一人尝试：
    任一待检定者已有检定就不再补挂，否则只为第一个待检定者补挂。不在队伍里的人不补。
    """
    out = []
    for entry in pending:
        waiting = tuple(cid for cid in entry.waiting_ids if cid in party_ids)
        if not waiting:
            continue
        if entry.shared:
            if not issued_ids.intersection(waiting):
                out.append((waiting[:1], str(entry.skill or "")))
            continue
        rest = tuple(cid for cid in waiting if cid not in issued_ids)
        if rest:
            out.append((rest, str(entry.skill or "")))
    return tuple(out)
