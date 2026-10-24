"""移动批次提交用例；依赖写入端口，不依赖 ORM 或展示服务。"""
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

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
