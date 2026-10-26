"""移动批次裁定：消费参与者快照及只读导航，不持有 ORM 或执行写入。"""
from collections.abc import Callable
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
    #: 含行动分句的整句；行动分句本身解析不出去向时才用（见 ``resolve_statement``）。
    context: tuple[str, ...] = ()


@dataclass(frozen=True)
class Proposal:
    intent: MovementIntent
    actor_ids: tuple[str, ...]
    #: 规划器漏报、按本人原话补出的提案：只知道想去哪，不知道通道有没有阻碍、要不要检定。
    inferred: bool = False


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


def resolve_statement(
    resolve: Callable[[str], Destination], sentences: tuple[str, ...], context: tuple[str, ...],
) -> Destination:
    """先只用行动分句解析去向；对不上时退一步用含行动的整句，且只采纳能唯一解析的结果。

    「推开东侧拱门前的石板，然后钻进去」「从墙洞钻回甬道，回到她身边」：去向写在同一句的
    另一分句里。只在退一步能解析成功时才采纳，所以不会把本来已解析或已判歧义的结果改坏。
    """
    dest = resolve("。".join(sentences))
    if dest.status == "resolved" or not context or tuple(context) == tuple(sentences):
        return dest
    wider = resolve("。".join(context))
    return wider if wider.status == "resolved" and wider.target else dest


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
    dest = resolve_statement(
        lambda text: navigation.resolve(lead.location, text, intent.exit),
        actor.sentences, actor.context,
    )
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
    if proposal.inferred:
        # 能确定去向不等于满足进入条件：补位提案没有障碍与检定信息，不落位、不带跟随者，
        # 交给 KP 按场景实际裁定（通行就调 scene_change，有阻碍就描写或要检定）。
        return replace(base, status="unconfirmed", skill="", reason="")
    followers = []
    for candidate in party:
        member = candidate.participant
        destination = None
        if member.stance == "act" and not member.travel_scene:
            result = resolve_statement(
                lambda text: navigation.resolve(lead.location, text),
                candidate.sentences, candidate.context,
            )
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


def fill_unproposed(
    proposals: tuple[Proposal, ...], party: tuple[ActorSnapshot, ...],
) -> tuple[Proposal, ...]:
    """规划器漏报的真人行动宣言：按本人原话补一条提案，排在规划器提案之后。

    只补真人、本人原话是明确行动（不含仅表态跟随）、不是大地图前往、且没有出现在任何规划器提案里
    （无论 stance）的人——规划器明确判过的（走近、商量、受阻……）照旧以规划器为准。
    补出的提案照常经证据、去向与可达复核，但永远不直接落位：去向明确时裁定为 ``unconfirmed``，
    由 KP 裁定通道（见 ``adjudicate_one``），歧义或找不到出口时照常请 KP 澄清。
    """
    covered = {cid for proposal in proposals for cid in proposal.actor_ids}
    added = tuple(
        Proposal(MovementIntent(stance="act", solo=actor.participant.solo),
                 (actor.participant.id,), inferred=True)
        for actor in party
        if actor.participant.human and actor.participant.stance == "act"
        and not actor.participant.travel_scene and actor.participant.id not in covered
    )
    return proposals + added


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
