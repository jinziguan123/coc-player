"""门控移动批次结算契约：不用数据库，直接以存档格式快照验证个人/共享结算与写回。"""
import pytest

from app.application.movement_settlement import settle_pending_checks
from app.application.movement_state import MoveRecord, PendingMove
from app.domain.checks import skill_matches
from app.domain.movement import CheckOutcome


def _gate(movers, *, anchor=1, skill="攀爬", shared=False, target="room", chars=None, **extra):
    states = chars or {cid: "pending" for cid in movers}
    entry = PendingMove.from_record({
        "anchor": anchor, "movers": list(movers), "origin": "hall", "target": target,
        "skill": skill, "exit": "墙洞", "shared": shared, "chars": dict(states), **extra,
    })
    record = MoveRecord.from_record({
        "anchor": anchor, "status": "pending_check", "movers": list(movers),
        "mover_names": [m.upper() for m in movers], "origin": "hall", "target": target,
        "stayers": [], "exit": "墙洞", "skill": skill, "reason": "", "clues": [],
        "char_status": dict(states), "shared": shared, **extra,
    })
    return entry, record


def _settle(gates, rollers, ok, *, skill="攀爬", locations=None, extra_records=()):
    pending = tuple(g[0] for g in gates)
    records = tuple(g[1] for g in gates) + tuple(extra_records)
    ids = {cid for entry in pending for cid in entry.mover_ids}
    return settle_pending_checks(
        pending, records, skill=skill, outcome=CheckOutcome(frozenset(rollers), ok),
        locations=locations if locations is not None else dict.fromkeys(ids, "hall"),
    )


def _states(settlement):
    return [r.to_record()["char_status"] for r in settlement.state.records]


def test_多人个人检定_一人成功只移动他_其余仍待检定():
    result = _settle([_gate(["a", "b"])], {"a"}, True)
    assert [(c.target, c.mover_ids) for c in result.commands] == [("room", ("a",))]
    assert _states(result) == [{"a": "moved", "b": "pending"}]
    assert result.state.records[0].status == "pending_check"
    (left,) = result.state.pending
    assert left.waiting_ids == ("b",)
    assert result.rollers == (("a",),)


def test_个人检定失败结束本人_他人之后成功不带走失败者():
    first = _settle([_gate(["a", "b"])], {"a"}, False)
    assert first.commands == () and _states(first) == [{"a": "failed", "b": "pending"}]
    gate = (first.state.pending[0], first.state.records[0])
    second = _settle([gate], {"b"}, True)
    assert [c.mover_ids for c in second.commands] == [("b",)]
    assert _states(second) == [{"a": "failed", "b": "moved"}]
    assert second.state.records[0].status == "partial" and second.state.pending == ()


def test_共享障碍_先失败者在他人成功时一起通过_已离场者不被搬回():
    first = _settle([_gate(["a", "b", "c"], shared=True)], {"a"}, False)
    assert _states(first) == [{"a": "tried", "b": "pending", "c": "pending"}]
    assert first.state.records[0].status_of("a") == "pending_check"   # tried 对守卫仍是待检定
    gate = (first.state.pending[0], first.state.records[0])
    second = _settle([gate], {"b"}, True, locations={"a": "hall", "b": "hall", "c": "elsewhere"})
    assert [c.mover_ids for c in second.commands] == [("a", "b")]
    assert _states(second) == [{"a": "moved", "b": "moved", "c": "failed"}]
    assert second.state.pending == ()


def test_共享障碍_全员尝试失败才整体失败():
    gate = _gate(["a", "b"], shared=True)
    first = _settle([gate], {"a"}, False)
    second = _settle([(first.state.pending[0], first.state.records[0])], {"b"}, False)
    assert _states(second) == [{"a": "failed", "b": "failed"}]
    assert second.state.records[0].status == "failed" and second.state.pending == ()


def test_重复结果和无关结果没有副作用():
    first = _settle([_gate(["a", "b"])], {"a"}, False)
    gate = (first.state.pending[0], first.state.records[0])
    assert _settle([gate], {"a"}, True) is None           # 已失败者重投不算
    assert _settle([gate], {"z"}, True) is None           # 不在条目里的人
    assert _settle([gate], {"b"}, True, skill="侦查") is None
    assert _settle([gate], set(), True) is None           # 不知道谁掷的不结算


def test_不在队伍快照里的人没有通过资格():
    result = _settle([_gate(["a", "b"], shared=True)], {"a"}, True, locations={"a": "hall"})
    assert [c.mover_ids for c in result.commands] == [("a",)]
    assert _states(result) == [{"a": "moved", "b": "failed"}]


def test_同一结果结算多条匹配条目_不同技能与无关记录保持原样():
    climb = _gate(["a"], anchor=1)
    climb2 = _gate(["b"], anchor=2, target="attic")
    strength = _gate(["a"], anchor=3, skill="力量")
    legacy = MoveRecord.from_record({"anchor": 0, "status": "discuss", "movers": ["a"]})
    result = _settle([climb, climb2, strength], {"a", "b"}, True, extra_records=(legacy,))
    assert [(c.target, c.mover_ids) for c in result.commands] == [("room", ("a",)), ("attic", ("b",))]
    assert [e.anchor for e in result.state.pending] == [3]
    assert result.state.pending[0].to_record() == strength[0].to_record()
    out = [r.to_record() for r in result.state.records]
    assert out[2] == strength[1].to_record() and out[3] == legacy.to_record()
    assert "char_status" not in out[3]


def test_写回保留扩展字段且不改输入快照():
    gate = _gate(["a", "b"], ext={"note": ["保留"]})
    before = (gate[0].to_record(), gate[1].to_record())
    result = _settle([gate], {"a"}, True)
    assert result.state.pending[0].to_record()["ext"] == {"note": ["保留"]}
    assert result.state.records[0].to_record()["ext"] == {"note": ["保留"]}
    assert (gate[0].to_record(), gate[1].to_record()) == before


@pytest.mark.parametrize(("expected", "rolled", "hit"), [
    ("攀爬", "攀爬", True), ("攀 爬", "攀爬", True), ("Climb", "climb", True),
    ("力量", "力量检定", True), ("攀爬", "侦查", False), ("", "攀爬", False), ("攀爬", "", False),
])
def test_技能匹配忽略空白大小写并允许互相包含(expected, rolled, hit):
    assert skill_matches(expected, rolled) is hit


def _entry(movers, chars=None, shared=False, skill="攀爬"):
    return PendingMove.from_record({"movers": list(movers), "skill": skill, "shared": shared,
                                    **({"chars": chars} if chars else {})})


def test_补挂检定_个人检定只给还没有检定的人():
    from app.application.movement_settlement import checks_to_issue

    pending = (_entry(["a", "b", "c"], {"a": "pending", "b": "pending", "c": "failed"}),)
    assert checks_to_issue(pending, party_ids=frozenset("abc"), issued_ids=frozenset("a")) == (
        (("b",), "攀爬"),)
    assert checks_to_issue(pending, party_ids=frozenset("abc"), issued_ids=frozenset("ab")) == ()


def test_补挂检定_共同障碍任一人已有检定就不补_否则只补第一个待检定者():
    from app.application.movement_settlement import checks_to_issue

    pending = (_entry(["a", "b", "c"], {"a": "tried", "b": "pending", "c": "pending"},
                      shared=True, skill="力量"),)
    party = frozenset("abc")
    assert checks_to_issue(pending, party_ids=party, issued_ids=frozenset()) == ((("b",), "力量"),)
    assert checks_to_issue(pending, party_ids=party, issued_ids=frozenset("c")) == ()
    # tried 的人已用掉尝试，他本人的旧检定不算“已为待检定者挂出”
    assert checks_to_issue(pending, party_ids=party, issued_ids=frozenset("a")) == ((("b",), "力量"),)


def test_补挂检定_不在队伍里的人不补_旧条目缺个人状态按全员待检定():
    from app.application.movement_settlement import checks_to_issue

    pending = (_entry(["x", "a"]), _entry(["y"]))
    assert checks_to_issue(pending, party_ids=frozenset("a"), issued_ids=frozenset()) == (
        (("a",), "攀爬"),)
