"""待检定移动快照：兼容旧字段、隔离可变存档、不丢扩展数据。"""
from copy import deepcopy

import pytest

from app.application.movement_state import PendingMove


@pytest.mark.parametrize("chars", [None, {}])
def test_旧存档缺少个人状态时按移动者补全(chars):
    raw = {"anchor": 3, "movers": ["b", "a"]}
    if chars is not None:
        raw["chars"] = chars
    entry = PendingMove.from_record(raw)
    assert entry.waiting_ids == ("b", "a")
    assert entry.to_record() == dict(raw, chars={"b": "pending", "a": "pending"})
    assert entry.shared is False


def test_有个人状态时不覆盖失败或共同障碍尝试结果():
    raw = {"movers": ["a", "b", "c"], "shared": True,
           "chars": {"a": "tried", "b": "pending", "c": "failed"}}
    entry = PendingMove.from_record(raw)
    assert entry.waiting_ids == ("b",)
    assert entry.to_record() == raw


def test_输入输出隔离_状态替换不改原快照并保留扩展字段():
    raw = {"anchor": 9, "movers": ["a", "b"], "origin": "hall", "target": "room",
           "skill": "攀爬", "exit": "洞口", "chars": {"a": "pending", "b": "pending"},
           "extra": {"notes": ["保留"]}}
    expected = deepcopy(raw)
    entry = PendingMove.from_record(raw)
    raw["extra"]["notes"].append("外部修改")
    raw["chars"]["a"] = "failed"
    raw["movers"].clear()
    updated = entry.with_states((("a", "moved"), ("b", "pending")))
    result = updated.to_record()
    assert result == dict(expected, chars={"a": "moved", "b": "pending"})
    result["extra"]["notes"].clear()
    assert entry.to_record() == expected
    assert updated.waiting_ids == ("b",)
