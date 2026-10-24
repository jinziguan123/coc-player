"""移动提交端口契约：提交前不释放凭据，纯状态批次也必须提交。"""
from contextlib import contextmanager

import pytest

from app.application.movement_execution import (
    MoveCommand, MovementStateUpdate, commit_movement_batch,
)
from app.application.movement_state import MoveRecord, PendingMove


class MemoryWriter:
    def __init__(self, fail_at=""):
        self.fail_at = fail_at
        self.calls = []

    def step(self, name):
        self.calls.append(name)
        if self.fail_at == name:
            raise RuntimeError(name)

    @contextmanager
    def transaction(self):
        self.step("begin")
        try:
            yield
            self.step("commit")
        except BaseException:
            self.calls.append("rollback")
            raise

    def stage(self, command):
        self.step("stage")
        return command if command.mover_ids else None

    def save_state(self, update):
        self.step("save")
        self.saved = update

    def refresh(self):
        self.step("refresh")


def test_提交后才刷新并释放按顺序排列的凭据():
    writer = MemoryWriter()
    first = MoveCommand("room", ("a",))
    second = MoveCommand("hall", ("b",), announce=False)
    assert commit_movement_batch(writer, (first, MoveCommand("room", ()), second),
                                 state=MovementStateUpdate((), ())) == (first, second)
    assert writer.calls == ["begin", "stage", "stage", "stage", "save", "commit", "refresh"]


def test_失败检定等无移动批次仍保存状态():
    writer = MemoryWriter()
    assert commit_movement_batch(writer, (), state=MovementStateUpdate((), ())) == ()
    assert writer.calls == ["begin", "save", "commit", "refresh"]


@pytest.mark.parametrize("failure", ["stage", "save", "commit"])
def test_写入或提交失败向调用者抛错_不刷新不释放凭据(failure):
    writer = MemoryWriter(failure)
    with pytest.raises(RuntimeError, match=failure):
        commit_movement_batch(writer, (MoveCommand("room", ("a",)),),
                              state=MovementStateUpdate((), ()))
    assert writer.calls[-1] == "rollback"
    assert "refresh" not in writer.calls


def test_直接移动不覆盖回合记录():
    writer = MemoryWriter()
    commit_movement_batch(writer, (MoveCommand("room", ("a",)),))
    assert "save" not in writer.calls


def test_状态更新以类型化快照交给写入端口_由适配器负责编码():
    writer = MemoryWriter()
    record = MoveRecord.new(anchor=1, status="pending_check", mover_ids=("a",), mover_names=("甲",),
                            origin="hall", target="room", states=(("a", "pending"),), shared=False)
    entry = PendingMove.new(anchor=1, mover_ids=("a",), origin="hall", target="room",
                            skill="攀爬", exit="", shared=False)
    update = MovementStateUpdate((record,), (entry,))
    commit_movement_batch(writer, (), state=update)
    assert writer.saved is update
    assert isinstance(writer.saved.records[0], MoveRecord)
    assert isinstance(writer.saved.pending[0], PendingMove)
