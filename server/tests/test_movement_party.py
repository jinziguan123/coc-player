"""参与者选择契约：不替沉默真人做决定，不跨场景拉人。"""
from dataclasses import replace

import pytest

from app.domain.movement_party import Participant, default_leader, proposal_leader, select_followers


HOST = Participant("host", "hall", True)
HUMAN = Participant("human", "hall", True)
AI = Participant("ai", "hall", False)


def followers(*members, solo=False, **kw):
    return select_followers((HOST, *members), leader_id="host", host_id="host",
                            origin="hall", solo=solo, collective=False, target="room", **kw)


def test_沉默真人不被点名或默认跟随带走_AI照常跟随():
    assert followers(HUMAN, AI, named=frozenset({"human"})) == ("ai",)
    assert followers(replace(HUMAN, stance="follow"), AI) == ("human", "ai")


def test_独行不会带走沉默AI_明确跟随者仍按自己的行动同行():
    assert followers(AI, solo=True) == ()
    assert followers(replace(AI, stance="follow"), solo=True) == ("ai",)


@pytest.mark.parametrize("member", [
    replace(AI, location="elsewhere"),
    replace(AI, stance="negated"),
    replace(AI, stance="discuss"),
    replace(AI, stance="conditional"),
    replace(AI, stance="approach"),
    replace(AI, solo=True),
    replace(AI, hesitant=True),
    replace(AI, stance="act", destination="other"),
])
def test_位置与个人表态优先于默认跟随(member):
    assert followers(member) == ()


def test_留守及已有裁定不被另一条移动卷走():
    active = replace(AI, stance="follow")
    assert followers(active, stayed=frozenset({"ai"})) == ()
    assert followers(active, busy=frozenset({"ai"})) == ()


def test_非房主可带明确点名的AI_不能带沉默真人():
    leader = replace(HUMAN, stance="act")
    assert select_followers((leader, HOST, AI), leader_id="human", host_id="host",
                            origin="hall", solo=False, collective=False) == ()
    assert select_followers((leader, HOST, AI), leader_id="human", host_id="host",
                            origin="hall", solo=False, collective=False,
                            named=frozenset({"host", "ai"})) == ("ai",)
    assert select_followers((leader, HOST, AI), leader_id="human", host_id="host",
                            origin="hall", solo=False, collective=True) == ("ai",)


def test_默认带头人选择真人行动者_排除目标冲突及仅跟随者():
    party = (replace(HOST, stance="act", destination="other"),
             replace(AI, stance="act"), replace(HUMAN, stance="act", destination="room"))
    assert default_leader(party, target="room", host_id="host") == "human"
    assert default_leader((replace(HUMAN, stance="act"), replace(HOST, stance="act")),
                          target="room", host_id="host") == "host"
    assert default_leader(party, target="room", host_id="host",
                          stayed=frozenset({"human"})) == "ai"
    assert default_leader((replace(HUMAN, stance="follow"),), target="room", host_id="host") is None
    assert default_leader((HOST,), target="room", host_id="host", fallback_id="host") == "host"


def test_提案点名者不能借用队友原话():
    party = (HOST, replace(AI, stance="act"))
    assert proposal_leader(party, named_ids=("host",)) == "host"
    assert proposal_leader(party) == "ai"
    assert proposal_leader((HOST, HUMAN)) is None


def test_提案带头人行动优先于真人_同级尊重点名顺序():
    assert proposal_leader((replace(HOST, stance="discuss"), replace(AI, stance="act"))) == "ai"
    party = (replace(AI, stance="act"), replace(HUMAN, stance="follow"),
             replace(HOST, stance="act"))
    assert proposal_leader(party) == "human"
    assert proposal_leader(party, named_ids=("host", "human", "ai")) == "host"
    assert proposal_leader((HOST, HUMAN), named_ids=("human", "host")) == "human"


def test_大地图表态能成为提案候选_交给后续证据门槛忽略():
    assert proposal_leader((HOST, replace(AI, travel_scene="room"))) == "ai"
