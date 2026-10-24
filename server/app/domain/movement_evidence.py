"""移动提案与玩家原话的证据门槛；先判定是否行动，再允许解析出口。"""
from dataclasses import dataclass
from typing import Literal


NON_MOVING = frozenset({
    "discuss", "conditional", "approach", "negated", "blocked", "ambiguous", "unreachable",
    "unresolved", "failed", "no_evidence",
})


@dataclass(frozen=True)
class EvidenceDecision:
    action: Literal["ignore", "stop", "resolve"]
    status: str = "no_evidence"


def assess_evidence(*, stance: str, travel_scene: str, planned_stance: str) -> EvidenceDecision:
    """大地图前往单独处理；任一方未认定行动，就不进入空间解析。"""
    if travel_scene:
        return EvidenceDecision("ignore")
    if stance not in ("act", "follow"):
        return EvidenceDecision("stop", stance if stance in NON_MOVING else "no_evidence")
    if planned_stance != "act":
        return EvidenceDecision("stop", planned_stance)
    return EvidenceDecision("resolve")
