"""用只读导航替身验证完整裁定顺序与多人批次，不需要数据库或模型。"""
from dataclasses import replace

import pytest

from app.application.movement_adjudication import ActorSnapshot, Proposal, adjudicate_batch
from app.domain.movement import MovementIntent
from app.domain.movement_party import Participant
from app.domain.movement_space import Destination


class Map:
    def __init__(self, status="resolved", target="room", reachable=True):
        self.status, self.target, self.can_reach = status, target, reachable
        self.calls = []

    def resolve(self, origin, text, exit_hint=""):
        self.calls.append("resolve")
        return Destination(self.status, self.target, clues=["洞口"])

    def reachable(self, origin, target):
        self.calls.append("reachable")
        return self.can_reach

    def obstruction_reason(self, origin, target):
        self.calls.append("reason")
        return "须先经过走廊"


def actor(cid="a", stance="act", **kw):
    return ActorSnapshot(Participant(cid, "hall", True, stance=stance, **kw), ("钻进去",))


def run(nav, *, party=None, intent=None, **kw):
    return adjudicate_batch((Proposal(intent or MovementIntent(), ("a",)),),
                            party or (actor(),), nav, host_id="a", stayed=frozenset(), **kw)


@pytest.mark.parametrize("stance", ["none", "discuss", "conditional", "negated", "approach"])
def test_原话否决时不调用导航(stance):
    nav = Map()
    ((decision, _),) = run(nav, party=(actor(stance=stance),))
    assert decision.status == ("no_evidence" if stance == "none" else stance)
    assert decision.mover_ids == ("a",) and not nav.calls


def test_大地图前往忽略_规划器未认可停止():
    nav = Map()
    assert run(nav, party=(actor(travel_scene="room"),)) == ()
    assert run(nav, intent=MovementIntent(stance="conditional"))[0][0].status == "conditional"
    assert nav.calls == []


@pytest.mark.parametrize("status,target,reachable,blocked,skill,expected,calls", [
    ("ambiguous", None, True, "", "", "ambiguous", ["resolve"]),
    ("resolved", "hall", True, "", "", None, ["resolve"]),
    ("resolved", "room", False, "锁门", "力量", "unreachable", ["resolve", "reachable", "reason"]),
    ("resolved", "room", True, "锁门", "力量", "blocked", ["resolve", "reachable"]),
    ("resolved", "room", True, "", "力量", "pending_check", ["resolve", "reachable", "resolve"]),
])
def test_解析通行检定依次执行_无效分支不提前查询(status, target, reachable, blocked, skill, expected, calls):
    nav = Map(status, target, reachable)
    result = run(nav, intent=MovementIntent(blocked=blocked, check_skill=skill))
    assert nav.calls == calls
    if expected is None:
        assert result == ()
    else:
        decision = result[0][0]
        assert decision.status == expected and decision.clues == ("洞口",)
        if expected == "unreachable":
            assert decision.reason == "须先经过走廊" and not decision.skill


def test_已被裁定的人不再参与后续提案_同批次使用原位置快照():
    party = (actor(), actor("b"))
    proposals = (Proposal(MovementIntent(solo=True), ("a",)),
                 Proposal(MovementIntent(solo=True), ("b",)))
    decisions = adjudicate_batch(proposals, party, Map(), host_id="a", stayed=frozenset())
    # B 明确表态行动，即使 A 说独行，B 仍按自身行动跟随；后续 B 的提案被占用规则排除。
    assert len(decisions) == 1
    assert decisions[0][0].mover_ids == ("a", "b")
    assert all(a.participant.location == "hall" for a in party)


def test_受阻裁定也占用角色_不同角色仍可独立裁定():
    proposals = (Proposal(MovementIntent(blocked="石板"), ("a",)),
                 Proposal(MovementIntent(), ("a",)), Proposal(MovementIntent(), ("b",)))
    result = adjudicate_batch(proposals, (actor(), actor("b")), Map(),
                              host_id="a", stayed=frozenset())
    assert [(d.status, d.mover_ids) for d, _ in result] == [("blocked", ("a",)), ("moved", ("b",))]


def test_旧提案不启用共同障碍_沿用旧技能门控():
    result = run(Map(), intent=MovementIntent(shared_check=True), legacy=True, legacy_gate="攀爬")
    assert result[0][0].skill == "攀爬" and result[0][1] is False
    assert run(Map(), intent=MovementIntent(shared_check=True))[0][1] is True


def test_沉默真人留在原处_AI按房主默认跟随():
    silent_human = actor("b", stance="none")
    ai = actor("c", stance="none")
    ai = replace(ai, participant=replace(ai.participant, human=False))
    decision = run(Map(), party=(actor(), silent_human, ai))[0][0]
    assert decision.mover_ids == ("a", "c")
    assert decision.stayer_ids == ("b",)
