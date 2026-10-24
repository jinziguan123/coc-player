"""移动行动证据的优先级与否决规则。"""
import pytest

from app.domain.movement_evidence import EvidenceDecision, assess_evidence


@pytest.mark.parametrize("stance", ["act", "follow", "negated", "none"])
def test_大地图前往不重复裁定(stance):
    assert assess_evidence(stance=stance, travel_scene="room", planned_stance="act") == \
        EvidenceDecision("ignore")


@pytest.mark.parametrize("stance", ["discuss", "conditional", "approach", "negated"])
def test_规划器不能把未行动的原话升级为移动(stance):
    assert assess_evidence(stance=stance, travel_scene="", planned_stance="act") == \
        EvidenceDecision("stop", stance)


@pytest.mark.parametrize("stance", ["none", "unknown"])
def test_没有行动证据就停止(stance):
    assert assess_evidence(stance=stance, travel_scene="", planned_stance="act") == \
        EvidenceDecision("stop", "no_evidence")


@pytest.mark.parametrize("stance", ["act", "follow"])
def test_原话行动仍须规划器认可(stance):
    assert assess_evidence(stance=stance, travel_scene="", planned_stance="act") == \
        EvidenceDecision("resolve")
    assert assess_evidence(stance=stance, travel_scene="", planned_stance="conditional") == \
        EvidenceDecision("stop", "conditional")


def test_双方都否定行动时保留原话原因():
    assert assess_evidence(stance="negated", travel_scene="", planned_stance="discuss") == \
        EvidenceDecision("stop", "negated")
