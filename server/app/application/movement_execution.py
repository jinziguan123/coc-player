"""移动批次提交用例；依赖写入端口，不依赖 ORM 或展示服务。"""
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar
from collections.abc import Mapping

from app.application.movement_adjudication import Decision
from app.application.movement_state import MoveRecord, PendingMove


@dataclass(frozen=True)
class MoveCommand:
    target: str
    mover_ids: tuple[str, ...]
    announce: bool = True


@dataclass(frozen=True)
class MovementStateUpdate:
    """本回合裁定记录与仍待检定的移动的完整新值；编码、存档键及 ORM 字段由适配器决定。"""
    records: tuple[MoveRecord, ...]
    pending: tuple[PendingMove, ...]


@dataclass(frozen=True)
class PreparedMovement:
    commands: tuple[MoveCommand, ...]
    state: MovementStateUpdate


def prepare_movement_batch(
    decisions: tuple[tuple[Decision, bool], ...], *, anchor: int,
    names: Mapping[str, str], pending: tuple[PendingMove, ...],
) -> PreparedMovement:
    """把值裁定装配成提交批次；只有新的待检定条目替换参与者重叠的旧条目。"""
    commands = []
    records = []
    new_pending = []
    for decision, shared in decisions:
        states = None
        record_shared = None
        if decision.status == "moved":
            commands.append(MoveCommand(decision.target, decision.mover_ids))
            states = tuple((cid, "moved") for cid in decision.mover_ids)
        elif decision.status == "pending_check":
            states = tuple((cid, "pending") for cid in decision.mover_ids)
            record_shared = shared
            new_pending.append(PendingMove.new(
                anchor=anchor, mover_ids=decision.mover_ids, origin=decision.origin,
                target=decision.target, skill=decision.skill, exit=decision.exit, shared=shared,
            ))
        records.append(MoveRecord.new(
            anchor=anchor, status=decision.status, mover_ids=decision.mover_ids,
            mover_names=tuple(names[cid] for cid in decision.mover_ids),
            origin=decision.origin, target=decision.target,
            stayer_names=tuple(names[cid] for cid in decision.stayer_ids),
            exit=decision.exit, skill=decision.skill, reason=decision.reason,
            clues=decision.clues, states=states, shared=record_shared,
        ))
    taken = {cid for entry in new_pending for cid in entry.mover_ids}
    retained = tuple(entry for entry in pending if not taken.intersection(entry.mover_ids))
    return PreparedMovement(tuple(commands), MovementStateUpdate(
        tuple(records), retained + tuple(new_pending),
    ))


Receipt = TypeVar("Receipt")


class MovementWriter(Protocol, Generic[Receipt]):
    def transaction(self) -> AbstractContextManager[None]: ...
    def stage(self, command: MoveCommand) -> Receipt | None: ...
    def save_state(self, update: MovementStateUpdate) -> None: ...
    def refresh(self) -> None: ...


def commit_movement_batch(
    writer: MovementWriter[Receipt], commands: tuple[MoveCommand, ...], *,
    state: MovementStateUpdate | None = None,
) -> tuple[Receipt, ...]:
    """一笔事务执行全部移动及状态写入；提交成功才刷新并返回展示凭据。

    空移动批次仍可保存失败检定等状态。stage 只能暂存，不能提交或广播。
    """
    receipts = []
    with writer.transaction():
        for command in commands:
            receipt = writer.stage(command)
            if receipt is not None:
                receipts.append(receipt)
        if state is not None:
            writer.save_state(state)
    writer.refresh()
    return tuple(receipts)
