"""待检定移动与回合裁定快照：兼容旧字段、隔离可变存档、不丢扩展数据。"""
from copy import deepcopy

import pytest

from app.application.movement_state import (
    MoveRecord, PendingMove, busy_ids, load_move_records,
)


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


def _gate_record(**extra) -> dict:
    return {"anchor": 4, "status": "pending_check", "movers": ["a", "b"], "mover_names": ["甲", "乙"],
            "origin": "hall", "target": "room", "stayers": ["丙"], "exit": "墙洞", "skill": "攀爬",
            "reason": "", "clues": [], "shared": True,
            "char_status": {"a": "pending", "b": "pending"}, **extra}


def test_旧裁定记录缺字段时按整条状态解释且原样写回():
    raw = {"anchor": 1, "status": "blocked", "movers": ["a"], "target": None}
    record = MoveRecord.from_record(raw)
    assert record.status_of("a") == "blocked"
    assert record.status_of("陌生人") == "blocked"
    assert record.names_of(["a"]) == ()          # 旧记录没有 mover_names
    assert (record.skill, record.exit, record.clues, record.states) == ("", "", (), ())
    assert record.to_record() == raw              # 未更新：不凭空补 char_status/status


def test_个人状态优先于整条状态_tried对外仍是待检定():
    record = MoveRecord.from_record(_gate_record(
        status="partial", char_status={"a": "tried", "b": "moved", "c": "failed"},
    ))
    assert record.status_of("a") == "pending_check"
    assert record.status_of("b") == "moved"
    assert record.status_of("c") == "failed"
    assert record.status_of("d") == "partial"     # 不在个人状态里的人沿用整条状态
    assert record.names_of(["b", "a"]) == ("甲", "乙")   # 名字按记录顺序给


@pytest.mark.parametrize(("states", "status"), [
    ((("a", "moved"),), "pending_check"),            # 只更新一人，另一人仍待检定
    ((("a", "moved"), ("b", "moved")), "moved"),
    ((("a", "failed"), ("b", "failed")), "failed"),
    ((("a", "moved"), ("b", "failed")), "partial"),
    ((("a", "tried"), ("b", "tried")), "pending_check"),
])
def test_更新个人状态会合并并重算整条状态(states, status):
    updated = MoveRecord.from_record(_gate_record()).with_states(states)
    assert updated.status == status
    result = updated.to_record()
    assert result["status"] == status
    assert result["char_status"] == {"a": "pending", "b": "pending", **dict(states)}


def test_旧裁定记录没有个人状态时更新只写入涉及的人():
    raw = {"anchor": 2, "status": "pending_check", "movers": ["a", "b"]}
    result = MoveRecord.from_record(raw).with_states((("a", "failed"),)).to_record()
    assert result == dict(raw, status="failed", char_status={"a": "failed"})


def test_裁定记录输入输出隔离_保留扩展字段():
    raw = _gate_record(ext={"notes": ["保留"]})
    expected = deepcopy(raw)
    record = MoveRecord.from_record(raw)
    raw["ext"]["notes"].append("外部修改")
    raw["char_status"]["a"] = "failed"
    raw["movers"].clear()
    assert record.mover_ids == ("a", "b") and record.status_of("a") == "pending_check"
    updated = record.with_states((("b", "moved"),))
    result = updated.to_record()
    assert result["ext"] == {"notes": ["保留"]} and result["shared"] is True
    result["ext"]["notes"].clear()
    result["char_status"]["a"] = "failed"
    assert updated.to_record()["ext"] == {"notes": ["保留"]}
    assert updated.status_of("a") == "pending_check"
    assert record.to_record() == expected


def test_裁定记录对应建立它的待检定移动():
    record = MoveRecord.from_record(_gate_record())
    assert record.concerns(PendingMove.from_record({"anchor": 4, "movers": ["b", "a"]}))
    assert not record.concerns(PendingMove.from_record({"anchor": 5, "movers": ["a", "b"]}))
    assert not record.concerns(PendingMove.from_record({"anchor": 4, "movers": ["a"]}))


def test_按锚点读取且已有裁定者不等于待检定名单():
    raws = [_gate_record(char_status={"a": "moved", "b": "failed"}, status="partial"),
            {"anchor": 7, "status": "discuss", "movers": ["c"]}]
    records = load_move_records(raws)
    assert [r.anchor for r in load_move_records(raws, 7)] == [7]
    assert load_move_records(None) == ()
    assert busy_ids(records) == {"a", "b", "c"}
    assert not any(r.status_of(cid) == "pending_check" for r in records for cid in r.mover_ids)


def test_新建裁定记录与旧手写格式逐字一致():
    base = {"anchor": 3, "status": "moved", "movers": ["a", "b"], "mover_names": ["甲", "乙"],
            "origin": "hall", "target": "room", "stayers": ["丙"], "exit": "墙洞", "skill": "",
            "reason": "", "clues": ["东侧"]}
    common = dict(anchor=3, mover_ids=("a", "b"), mover_names=("甲", "乙"), origin="hall",
                  target="room", stayer_names=("丙",), exit="墙洞", clues=("东侧",))
    moved = MoveRecord.new(status="moved", states=(("a", "moved"), ("b", "moved")), **common)
    assert moved.to_record() == dict(base, char_status={"a": "moved", "b": "moved"})
    gated = MoveRecord.new(status="pending_check", skill="攀爬",
                           states=(("a", "pending"), ("b", "pending")), shared=True, **common)
    assert gated.to_record() == dict(base, status="pending_check", skill="攀爬", shared=True,
                                     char_status={"a": "pending", "b": "pending"})
    assert gated.status_of("a") == "pending_check" and gated.names_of(["b"]) == ("乙",)
    blocked = MoveRecord.new(status="blocked", reason="石板", **common)
    assert blocked.to_record() == dict(base, status="blocked", reason="石板")


def test_新建待检定移动与旧手写格式逐字一致_并可直接结算():
    entry = PendingMove.new(anchor=3, mover_ids=("a", "b"), origin="hall", target="room",
                            skill="攀爬", exit="墙洞", shared=False)
    assert entry.to_record() == {"anchor": 3, "movers": ["a", "b"], "origin": "hall",
                                 "target": "room", "skill": "攀爬", "exit": "墙洞", "shared": False,
                                 "chars": {"a": "pending", "b": "pending"}}
    assert entry.waiting_ids == ("a", "b")
    assert entry.with_states((("a", "moved"), ("b", "pending"))).waiting_ids == ("b",)
