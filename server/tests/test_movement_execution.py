"""移动提交端口契约：提交前不释放凭据，纯状态批次也必须提交。"""
from contextlib import contextmanager

import pytest

from app.application.movement_execution import (
    MoveCommand, MovementStateUpdate, commit_movement_batch, prepare_movement_batch,
)
from app.application.movement_adjudication import Decision
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


def test_装配混合裁定_只有落位生成命令_门控暂存_拒绝只记结果():
    decisions = (
        (Decision("moved", ("b", "a"), "hall", "room", stayer_ids=("c",)), False),
        (Decision("pending_check", ("c",), "hall", "tower", skill="攀爬", exit="梯子"), True),
        (Decision("blocked", ("d",), "hall", "vault", reason="锁门"), True),
    )
    prepared = prepare_movement_batch(decisions, anchor=7,
                                      names={"a": "甲", "b": "乙", "c": "丙", "d": "丁"}, pending=())
    assert prepared.commands == (MoveCommand("room", ("b", "a")),)
    moved, gated, blocked = [r.to_record() for r in prepared.state.records]
    assert moved["movers"] == ["b", "a"] and moved["mover_names"] == ["乙", "甲"]
    assert moved["stayers"] == ["丙"] and moved["char_status"] == {"b": "moved", "a": "moved"}
    assert "shared" not in moved
    assert gated["char_status"] == {"c": "pending"} and gated["shared"] is True
    assert "char_status" not in blocked and "shared" not in blocked and blocked["reason"] == "锁门"
    assert prepared.state.pending[0].to_record() == {
        "anchor": 7, "movers": ["c"], "origin": "hall", "target": "tower", "skill": "攀爬",
        "exit": "梯子", "shared": True, "chars": {"c": "pending"},
    }


def test_新的待检定批次整体替换重叠旧条目_保留其他条目与扩展字段():
    overlap = PendingMove.from_record({"anchor": 1, "movers": ["a", "b"]})
    untouched = PendingMove.from_record({"anchor": 1, "movers": ["c"], "extension": {"x": [1]}})
    before = overlap.to_record(), untouched.to_record()
    prepared = prepare_movement_batch(
        ((Decision("pending_check", ("a",), "hall", "room", skill="力量"), False),),
        anchor=2, names={"a": "甲"}, pending=(overlap, untouched),
    )
    assert prepared.state.pending[0] is untouched
    assert [e.mover_ids for e in prepared.state.pending] == [("c",), ("a",)]
    assert (overlap.to_record(), untouched.to_record()) == before


@pytest.mark.parametrize("status", ["moved", "blocked"])
def test_非门控裁定沿用原规则不清除旧待检定条目(status):
    old = PendingMove.from_record({"anchor": 1, "movers": ["a"]})
    prepared = prepare_movement_batch(((Decision(status, ("a",), "hall", "room"), False),),
                                      anchor=2, names={"a": "甲"}, pending=(old,))
    assert prepared.state.pending == (old,)
