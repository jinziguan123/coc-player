"""移动参与者选择：仅使用位置、控制权及本轮表态的值快照。"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Participant:
    id: str
    location: str | None
    human: bool
    stance: str = "none"
    solo: bool = False
    hesitant: bool = False
    travel_scene: str = ""
    destination: str | None = None
    collective: bool = False

    @property
    def acts(self) -> bool:
        return self.stance in ("act", "follow")


def select_followers(
    party: tuple[Participant, ...], *, leader_id: str, host_id: str | None,
    origin: str | None, solo: bool, collective: bool, target: str | None = None,
    stayed: frozenset[str] = frozenset(), busy: frozenset[str] = frozenset(),
    named: frozenset[str] = frozenset(),
) -> tuple[str, ...]:
    """保持提案顺序；真人须自己表态，AI 可按既有房主跟随规则同行。"""
    followers = []
    for member in party:
        if member.id == leader_id or member.id in stayed or member.id in busy:
            continue
        if member.location != origin:
            continue
        if member.stance in ("discuss", "conditional", "negated", "approach") or member.solo:
            continue
        if member.hesitant and not member.acts:
            continue
        own = member.acts and not member.travel_scene
        if own and member.stance == "act" and target:
            if member.destination is not None and member.destination != target:
                continue
        if member.human:
            if own:
                followers.append(member.id)
            continue
        if own or (not solo and (member.id in named or collective or leader_id == host_id)):
            followers.append(member.id)
    return tuple(followers)


def default_leader(
    party: tuple[Participant, ...], *, target: str, host_id: str | None,
    stayed: frozenset[str] = frozenset(), fallback_id: str | None = None,
) -> str | None:
    """工具省略名单时，从直接宣告移动的人中选择带头人；去向冲突者不候选。"""
    candidates = [
        p for p in party if p.id not in stayed and p.stance == "act" and not p.travel_scene
        and (p.destination is None or p.destination == target)
    ]
    candidates.sort(key=lambda p: (not p.human, p.id != host_id))
    return candidates[0].id if candidates else fallback_id
