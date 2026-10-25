"""移动批次裁定：消费参与者快照及只读导航，不持有 ORM 或执行写入。"""
from dataclasses import dataclass, replace
from typing import Protocol

from app.domain.movement import MovementIntent, assess_passage
from app.domain.movement_evidence import assess_evidence
from app.domain.movement_party import Participant, proposal_leader, select_followers
from app.domain.movement_space import Destination


@dataclass(frozen=True)
class ActorSnapshot:
    participant: Participant
    sentences: tuple[str, ...]


@dataclass(frozen=True)
class Proposal:
    intent: MovementIntent
    actor_ids: tuple[str, ...]


@dataclass(frozen=True)
class Decision:
    status: str
    mover_ids: tuple[str, ...]
    origin: str | None
    target: str | None = None
    stayer_ids: tuple[str, ...] = ()
    exit: str = ""
    skill: str = ""
    reason: str = ""
    clues: tuple[str, ...] = ()


class Navigation(Protocol):
    def resolve(self, origin: str | None, text: str, exit_hint: str = "") -> Destination: ...
    def reachable(self, origin: str | None, target: str) -> bool: ...
    def obstruction_reason(self, origin: str | None, target: str) -> str: ...


def adjudicate_one(
    proposal: Proposal, party: tuple[ActorSnapshot, ...], navigation: Navigation, *,
    host_id: str | None, stayed: frozenset[str], busy: frozenset[str], legacy_gate: str,
) -> Decision | None:
    members = tuple(a.participant for a in party)
    leader_id = proposal_leader(members, named_ids=proposal.actor_ids)
    if leader_id is None:
        return None
    actor = next(a for a in party if a.participant.id == leader_id)
    lead = actor.participant
    intent = proposal.intent
    base = Decision("no_evidence", (leader_id,), lead.location, exit=intent.exit)
    evidence = assess_evidence(stance=lead.stance, travel_scene=lead.travel_scene,
                               planned_stance=intent.stance)
    if evidence.action == "ignore":
        return None
    if evidence.action == "stop":
        return replace(base, status=evidence.status)
    dest = navigation.resolve(lead.location, "。".join(actor.sentences), intent.exit)
    base = replace(base, clues=tuple(dest.clues))
    if dest.status != "resolved" or not dest.target:
        return replace(base, status=dest.status)
    target = dest.target
    if target == lead.location:
        return None
    passage = assess_passage(intent, reachable=navigation.reachable(lead.location, target),
                             legacy_gate=legacy_gate)
    base = replace(base, status=passage.status, target=target,
                   skill=passage.skill, reason=passage.reason)
    if passage.status == "unreachable":
        return replace(base, reason=navigation.obstruction_reason(lead.location, target))
    if passage.status == "blocked":
        return base
    followers = []
    for candidate in party:
        member = candidate.participant
        destination = None
        if member.stance == "act" and not member.travel_scene:
            result = navigation.resolve(lead.location, "。".join(candidate.sentences))
            if result.status == "resolved":
                destination = result.target
        followers.append(replace(member, destination=destination))
    ids = (leader_id, *select_followers(
        tuple(followers), leader_id=leader_id, host_id=host_id, origin=lead.location,
        solo=intent.solo or lead.solo, collective=lead.collective, target=target,
        stayed=stayed, busy=busy, named=frozenset(proposal.actor_ids) - {leader_id},
    ))
    return replace(base, mover_ids=ids, stayer_ids=tuple(
        member.id for member in members if member.id not in ids and member.location == lead.location
    ))


def adjudicate_batch(
    proposals: tuple[Proposal, ...], party: tuple[ActorSnapshot, ...], navigation: Navigation, *,
    host_id: str | None, stayed: frozenset[str], legacy: bool = False, legacy_gate: str = "",
) -> tuple[tuple[Decision, bool], ...]:
    """所有提案使用同一写入前快照；先到的裁定占用角色，后续冲突提案跳过。"""
    busy: set[str] = set()
    decisions = []
    for proposal in proposals:
        decision = adjudicate_one(proposal, party, navigation, host_id=host_id,
                                  stayed=stayed, busy=frozenset(busy), legacy_gate=legacy_gate)
        if decision is None or busy.intersection(decision.mover_ids):
            continue
        busy.update(decision.mover_ids)
        decisions.append((decision, bool(proposal.intent.shared_check) and not legacy))
    return tuple(decisions)
