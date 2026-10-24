"""自然语言移动：把规划器的结构化移动提案裁定成确定性的位置变更，并统一执行。

链路：玩家行动宣言 → 规划器 ``scene_policy.moves``（谁、经哪个出口、去哪、表态性质、独行/同行、
阻挡、检定）→ 本模块用「本轮玩家原话 + 移动者当前位置 + 模组连通/出口证据」复核：

- 通过的在 KP 动笔**之前**落位：叙事、NPC 在场、分头分组都以新位置为准，不会「叙事还在旧场景、
  最后才偷偷换地图」；
- 需要检定的暂存到骰子落地（成功才移动，失败不动；新回合开始即作废，不会被之后无关的投骰兑现）；
- 商量、条件句、走近观察、去向不明、不连通、受阻：一律不动人，只给 KP 一句权威的裁定说明。

去向解析不要求玩家说出房间正式名称：「拱门」「那个缺口」先按场景描述里的出口分句归属到相邻场景，
唯一且有依据才执行；多个出口都对得上就让 KP 澄清，绝不把所有「门」粗暴指到任意相邻场景。
模组可选地在场景里写 ``exits: [{"to": 场景id, "name": "墙洞", "aliases": [...]}]`` 精确声明出口；
没写的旧模组继续用描述与 ``connections`` 推断。

执行统一走 ``move_characters``：KP 原生工具、文本指令适配层（都落到 ``scene_change`` 执行器）、
规划器兜底与大地图前往共用。同一笔事务里先把留守者写实原位置、再移动移动者——位置是懒写入的，
主角一走 ``current_scene_id`` 就变，没有记录的人会被回落「带走」。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.application.movement_plans import movement_proposals as _proposals
from app.application.movement_space import project_scene, project_space
from app.domain.movement_language import Statement, analyze, has_any as _has
from app.domain.movement_space import Destination, resolve_destination as resolve_space_destination
from app.domain.movement import CheckOutcome, MovementIntent, rollup as _rollup, settle_check
from app.models.character import Character
from app.models.module import Module
from app.models.session import GameSession
from app.services import (
    check_gating,
    effect_channel,
    illustration_service,
    scene_kind,
    session_service,
    turn_context,
)
from app.services.event_protocol import make_chunk as _make_chunk

logger = logging.getLogger(__name__)

#: turn_state：本回合的移动裁定（按回合锚点区分；重新生成复用，不重复执行）。
MOVES_KEY = "turn_moves"
#: turn_state：被检定门控、等骰子落地的移动。新的玩家回合开始即作废。
PENDING_KEY = "pending_moves"

#: 已落位 / 不动人的各种裁定。
MOVED = "moved"
PENDING = "pending_check"
NON_MOVING = frozenset({
    "discuss", "conditional", "approach", "negated", "blocked", "ambiguous", "unreachable",
    "unresolved", "failed", "no_evidence",
})

# ── 空间快照适配：保留原服务入口，纯解析位于领域层 ──────────────

def scene_terms(module: Module | None, scene_id: str | None) -> set[str]:
    for scene in (module.scenes if module else None) or []:
        if str(scene.get("id") or "") == str(scene_id or ""):
            return set(project_scene(scene).terms)
    return set()


def _location_ids(module: Module | None) -> list[str]:
    return [
        str(s.get("id")) for s in scene_kind.location_scenes(module.scenes if module else [])
        if s.get("id")
    ]


def _candidate_scenes(module: Module | None, origin: str | None) -> list[str]:
    """从 origin 出发可经出口直达的场景：有连通图看邻居；整个模组没建图时退化为全部其他地点。"""
    neighbors = session_service.scene_neighbors(module, origin)
    if neighbors:
        return neighbors
    has_graph = any(
        (s.get("connections") or []) for s in scene_kind.location_scenes(module.scenes if module else [])
    )
    if has_graph:
        return []
    return [sid for sid in _location_ids(module) if sid != origin]


def resolve_destination(
    module: Module | None, origin: str | None, text: str, planner_exit: str = "",
) -> Destination:
    space = project_space(
        (module.scenes if module else None) or [],
        _location_ids(module), _candidate_scenes(module, origin),
    )
    return resolve_space_destination(space, origin, text, planner_exit)


# ── 本轮原话 ─────────────────────────────────────────────────────────

def turn_anchor(db: Session, session_id: str, pre_gen_seq: int | None) -> int:
    """回合锚点 = 本回合玩家输入之前那段 KP 旁白的序号（重新生成、投骰续写都不变）。"""
    events = session_service.get_session_events(db, session_id)
    seqs = [
        int(e.sequence_num or 0) for e in events
        if e.event_type == "narration"
        and (pre_gen_seq is None or int(e.sequence_num or 0) <= pre_gen_seq)
    ]
    return max(seqs, default=-1)


def turn_statements(
    db: Session, session_id: str, party: list[Character], pre_gen_seq: int | None,
    named_terms: set[str] | None = None,
) -> tuple[dict[str, Statement], set[str]]:
    """本轮各成员自己的原话 → Statement；以及本轮明确留守（stay 标记）的角色 id。"""
    ids = {c.id for c in party}
    texts: dict[str, list[str]] = {}
    travel: dict[str, str] = {}
    stayed: set[str] = set()
    for ev in turn_context.guard_turn_events(db, session_id, pre_gen_seq):
        if ev.actor_id not in ids:
            continue
        meta = ev.metadata_ or {}
        if meta.get(session_service.STAY_META_KEY):
            stayed.add(ev.actor_id)
        if meta.get("travel") and meta.get("scene_id"):
            travel[ev.actor_id] = str(meta["scene_id"])
            continue
        if ev.event_type in ("action", "dialogue") and (ev.content or "").strip():
            texts.setdefault(ev.actor_id, []).append(ev.content.strip())
    out: dict[str, Statement] = {}
    for cid in ids:
        stmt = analyze("。".join(texts.get(cid, [])), named_terms)
        if cid in travel:
            stmt.travel_scene = travel[cid]
            stmt.kind = "act"
        out[cid] = stmt
    return out, stayed


def has_move_evidence(
    db: Session, session_id: str, module: Module | None, game_session: GameSession,
    char: Character, target: str, pre_gen_seq: int | None = None,
) -> bool:
    """该角色本轮是否确有前往 target 的移动：地图前往、本轮已落位的裁定，或原话行动宣言能解析到 target。"""
    if any(
        r.get("target") == target and char_status(r, char.id) == MOVED
        for r in turn_records(game_session)
    ):
        return True
    # 本轮已经被搬过去了（KP 工具 / 门控结算落的 move 事件）：那次移动本身就是证据。
    for ev in turn_context.guard_turn_events(db, session_id, pre_gen_seq):
        meta = ev.metadata_ or {}
        if meta.get("kind") == "move" and meta.get("scene_id") == target and char.id in (
            meta.get("movers") or []
        ):
            return True
    terms = scene_terms(module, target)
    stmts, _stayed = turn_statements(db, session_id, [char], pre_gen_seq, terms)
    stmt = stmts.get(char.id) or Statement()
    if stmt.travel_scene:
        return stmt.travel_scene == target
    if stmt.kind != "act":
        return False
    text = "。".join(stmt.sentences)
    if _has(text, terms):
        return True   # 行动宣言里点名了目的地（「我走进图书馆」「（前往：七号车厢）」）
    origin = session_service.get_char_location(game_session, char.id)
    dest = resolve_destination(module, origin, text)
    return dest.status == "resolved" and dest.target == target


# ── 执行 ─────────────────────────────────────────────────────────────

def _names(chars: list[Character]) -> str:
    return "、".join(c.name for c in chars)


@dataclass
class _Staged:
    """一次已在事务内落位（flush、未提交）的移动；提交成功后才据此广播。"""

    target: str
    movers: list[Character]
    event_id: str | None = None
    text: str = ""
    meta: dict = field(default_factory=dict)


def _stage_move(
    db: Session, session_id: str, game_session: GameSession, module: Module | None,
    movers: list[Character], target: str, party: list[Character], *, announce: bool = True,
) -> _Staged | None:
    """在**调用方的事务**里落位：写实留守者、移动、落 move 事件——只 flush，不提交。

    事务归调用方所有（``effect_channel.transaction``）：位置、留守固定、事件、裁定记录与待执行
    条目的消费要么一起提交、要么一起回滚。幂等：已在目标的人跳过；都在了返回 None。

    主角（房主锚点）在移动者之列时 ``current_scene_id`` 会随之改变——同处、没有位置记录、
    又不跟着走的人先写实原位置，否则他们会被回落「带走」。
    """
    todo = [m for m in movers if session_service.get_char_location(game_session, m.id) != target]
    if not todo or not target:
        return None
    mover_ids = {m.id for m in todo}
    explicit = session_service.get_party_locations(game_session)
    pins: dict[str, str] = {}
    if game_session.player_character_id in mover_ids:
        for c in party:
            if c.id in mover_ids or c.id in explicit:
                continue
            here = session_service.get_char_location(game_session, c.id)
            if here:
                pins[c.id] = here
    for cid, here in pins.items():
        session_service.set_char_location(db, session_id, cid, here, commit=False)
    for m in todo:
        session_service.set_char_location(db, session_id, m.id, target, commit=False)
    staged = _Staged(target, todo)
    if announce:
        name = turn_context._scene_name(module, target) if module else target
        whole = {c.id for c in party} <= mover_ids or not party
        staged.text = f"场景切换至：{name}" if whole else f"场景切换至：{name}（{_names(todo)}）"
        # 系统事件让位置变化进存档与后续上下文（重新生成保留它，位置本身也不回滚）。
        staged.meta = {"kind": "move", "scene_id": target, "movers": [m.id for m in todo]}
        ev = session_service.add_event(
            db, session_id, "system", staged.text, actor_name="系统", metadata=staged.meta,
            commit=False,
        )
        staged.event_id = ev.id
    return staged


def _announce(
    db: Session, session_id: str, module: Module | None, staged: list[_Staged],
) -> list[str]:
    """提交**成功之后**的外部副作用：拼 chunk、补场景配图。配图失败只记日志——
    移动与事件已经提交，绝不能因此重做一遍或再写一条事件。"""
    chunks: list[str] = []
    for st in staged:
        if st.event_id is None:
            continue
        chunks.append(_make_chunk("system", st.text, metadata=st.meta, event_id=st.event_id))
        if module is not None:
            try:
                chunks += illustration_service._maybe_scene_illustration(
                    db, session_id, module, st.target,
                )
            except Exception:
                logger.exception("移动后的场景配图失败（移动已提交，不重试）：session=%s", session_id)
    if staged:
        chunks.append(_make_chunk("map_update", metadata={"reason": "move"}))
    return chunks


def move_characters(
    db: Session, session_id: str, game_session: GameSession, module: Module | None,
    movers: list[Character], target: str, party: list[Character], *, announce: bool = True,
) -> list[str]:
    """把 movers 移到 target：本函数拥有事务，一次提交；返回提交后要广播的 chunk。"""
    db.refresh(game_session)
    with effect_channel.transaction(db):
        staged = _stage_move(
            db, session_id, game_session, module, movers, target, party, announce=announce,
        )
    db.refresh(game_session)
    return _announce(db, session_id, module, [staged] if staged else [])


def travel(
    db: Session, session_id: str, game_session: GameSession, module: Module | None,
    actor: Character, target: str, party: list[Character],
) -> list[Character]:
    """大地图「前往」的落位：与自然语言移动同一套执行语义。返回实际移动的人。

    房主锚点前往时，同处的 AI 队友默认跟随（本轮说了留守/不去、或房主说了独行的除外），
    其他真人玩家不被代为决定、留在原处；非房主前往只搬他自己。不另发场景切换消息——
    前往入口自己落「（前往：X）」行动与配图卡。
    """
    origin = session_service.get_char_location(game_session, actor.id)
    movers = [actor]
    if actor.id == game_session.player_character_id:
        stmts, stayed = turn_statements(db, session_id, party, None)
        lead = stmts.get(actor.id) or Statement()
        movers += select_followers(
            db, session_id, game_session, actor, party, origin=origin, solo=lead.solo,
            collective=False, stmts=stmts, stayed=stayed, busy=busy_ids(game_session),
        )
    move_characters(db, session_id, game_session, module, movers, target, party, announce=False)
    return movers


def select_followers(
    db: Session, session_id: str, game_session: GameSession, leader: Character,
    party: list[Character], *, origin: str | None, solo: bool, collective: bool,
    stmts: dict[str, Statement], stayed: set[str], named: list[Character] | None = None,
    busy: set[str] | None = None, module: Module | None = None, target: str | None = None,
) -> list[Character]:
    """谁跟着 leader 走。只看与 leader 同处 origin 的人：

    - 本轮说过留守、或自己的原话是商量/条件/拒绝/走近/劝人等一等、自己的行动指向别处、或另有本轮移动裁定
      （busy）的 → 不跟；
    - 真人玩家：只有自己表态要走（行动或「我跟上」）才算，绝不替他决定；
    - AI 队友：被点名（named）、自己表态、leader 说了「我们/一起」，或 leader 是房主锚点且没说独行
      （队伍默认跟随房主）→ 跟；leader 明说独行时只有自己表态的才跟。
    """
    named_ids = {c.id for c in (named or [])}
    out: list[Character] = []
    for c in party:
        if c.id == leader.id or c.id in stayed or c.id in (busy or set()):
            continue
        if session_service.get_char_location(game_session, c.id) != origin:
            continue
        stmt = stmts.get(c.id) or Statement()
        if stmt.kind in ("discuss", "conditional", "negated", "approach") or stmt.solo:
            continue
        if stmt.hesitant and not stmt.acts:
            continue   # 正劝人等一等的人自己不会跟着走
        own = stmt.acts and not stmt.travel_scene
        if own and stmt.kind == "act" and target:
            dest = resolve_destination(module, origin, "。".join(stmt.sentences))
            if dest.status == "resolved" and dest.target != target:
                continue   # 他自己要去的是另一处：由他自己的那条提案裁定
        if session_service.is_human_controlled(db, session_id, c.id):
            if own:
                out.append(c)
            continue
        if own or (not solo and (
            c.id in named_ids or collective or leader.id == game_session.player_character_id
        )):
            out.append(c)
    return out


# ── 回合裁定记录 ─────────────────────────────────────────────────────

def turn_records(game_session: GameSession, anchor: int | None = None) -> list[dict]:
    records = list((game_session.turn_state or {}).get(MOVES_KEY) or [])
    if anchor is None:
        return records
    return [r for r in records if r.get("anchor") == anchor]


def _set_turn_state(game_session: GameSession, **updates) -> None:
    """在调用方事务里改 turn_state（不提交）。单独成函数，也是测试注入写入失败的接缝。"""
    ts = dict(game_session.turn_state or {})
    ts.update(updates)
    game_session.turn_state = ts


def char_status(record: dict, char_id: str) -> str:
    """某人在一条裁定里的状态：门控移动按人记（pending/tried/moved/failed），其余沿用整条的状态。

    tried＝共同障碍上他的尝试失败了，但障碍还可能被别人解除——对外仍是「待检定」，不是「失败」。
    """
    per = record.get("char_status") or {}
    if char_id in per:
        return {"pending": PENDING, "tried": PENDING, "moved": MOVED}.get(per[char_id], per[char_id])
    return str(record.get("status") or "")


def begin_turn(game_session: GameSession) -> bool:
    """新的玩家回合：作废检定门控的待执行移动（不提交，由 check_gating.begin_turn 一并提交）。"""
    ts = dict(game_session.turn_state or {})
    changed = bool(ts.pop(PENDING_KEY, None)) | bool(ts.pop(MOVES_KEY, None))
    if changed:
        game_session.turn_state = ts
    return changed


# ── 裁定 ─────────────────────────────────────────────────────────────

@dataclass
class Verdict:
    status: str
    movers: list[Character]
    origin: str | None = None
    target: str | None = None
    stayers: list[Character] = field(default_factory=list)
    exit: str = ""
    skill: str = ""
    reason: str = ""
    clues: list[str] = field(default_factory=list)

    def record(self, anchor: int) -> dict:
        return {
            "anchor": anchor, "status": self.status, "movers": [c.id for c in self.movers],
            "mover_names": [c.name for c in self.movers], "origin": self.origin,
            "target": self.target, "stayers": [c.name for c in self.stayers], "exit": self.exit,
            "skill": self.skill, "reason": self.reason, "clues": self.clues,
        }


def _match_chars(names: list[str], party: list[Character]) -> list[Character]:
    out: list[Character] = []
    for raw in names:
        name = str(raw or "").strip()
        if not name:
            continue
        hit = next((c for c in party if c.name == name), None) or next(
            (c for c in party if name in c.name or c.name in name), None,
        )
        if hit is not None and hit not in out:
            out.append(hit)
    return out


def _leader(
    db: Session, session_id: str, actors: list[Character], stmts: dict[str, Statement],
    party: list[Character],
) -> Character | None:
    """提案的带头人：规划器点了名就只在点名的人里找（不借别人的原话），没点名才在全队里找；
    按「有移动原话、真人」优先。"""
    for pool in ([actors] if actors else [party]):
        ranked = sorted(
            pool,
            key=lambda c: (
                not (stmts.get(c.id) or Statement()).acts,
                not session_service.is_human_controlled(db, session_id, c.id),
            ),
        )
        for c in ranked:
            stmt = stmts.get(c.id) or Statement()
            if stmt.kind != "none" or stmt.travel_scene:
                return c
    return actors[0] if actors else None


def _adjudicate_one(
    db: Session, session_id: str, game_session: GameSession, module: Module | None,
    proposal: MovementIntent, party: list[Character], stmts: dict[str, Statement],
    stayed: set[str], busy: set[str], *, legacy_gate: str,
) -> Verdict | None:
    actors = _match_chars(list(proposal.actors), party)
    leader = _leader(db, session_id, actors, stmts, party)
    if leader is None:
        return None
    stmt = stmts.get(leader.id) or Statement()
    origin = session_service.get_char_location(game_session, leader.id)
    base = Verdict("no_evidence", [leader], origin, exit=proposal.exit)
    if stmt.travel_scene:
        return None   # 大地图前往另有确定性入口（commit_pending_travel），这里不重复裁定
    # 表态性质：规划器与原话**都**认定是行动才算行动；任何一方说「人没动」就不动。
    if not stmt.acts:
        base.status = stmt.kind if stmt.kind in NON_MOVING else "no_evidence"
        return base
    if proposal.stance != "act":
        base.status = proposal.stance
        return base
    text = "。".join(stmt.sentences)
    dest = resolve_destination(module, origin, text, proposal.exit)
    base.clues = dest.clues
    if dest.status != "resolved" or not dest.target:
        base.status = dest.status
        return base
    target = dest.target
    base.target = target
    if target == origin:
        return None
    if session_service.find_scene_path(
        module, origin, target, via_allowed=session_service.passable_scene_ids(game_session),
    ) is None:
        base.status = "unreachable"
        blocker = session_service.travel_blocker(module, game_session, origin, target)
        base.reason = (
            f"要先经过{turn_context._scene_name(module, blocker[0])}（{blocker[1]}）" if blocker
            else "与当前位置不连通"
        )
        return base
    # 受阻只认规划器对**这条通道**的裁定（门锁着、石板挡着）。KP 标的 blocked_scenes 管的是
    # 「借道穿过」，上面的寻路已按可通行集合把关；把它当终点仍是玩家的自由，与大地图前往一致。
    if (proposal.blocked or "").strip():
        base.status = "blocked"
        base.reason = proposal.blocked.strip()
        return base
    solo = proposal.solo or stmt.solo
    followers = select_followers(
        db, session_id, game_session, leader, party, origin=origin, solo=solo,
        collective=stmt.collective, stmts=stmts, stayed=stayed,
        named=[a for a in actors if a.id != leader.id], busy=busy, module=module, target=target,
    )
    movers = [leader, *followers]
    base.movers = movers
    base.stayers = [
        c for c in party
        if c not in movers and session_service.get_char_location(game_session, c.id) == origin
    ]
    base.skill = (proposal.check_skill or "").strip() or legacy_gate
    base.status = PENDING if base.skill else MOVED
    return base


def _note(module: Module | None, verdict: Verdict) -> str:
    """给 KP 的一句权威裁定。只给 KP 看（目标名 KP 本就知道），玩家看不到。"""
    who = _names(verdict.movers)
    here = turn_context._scene_name(module, verdict.origin) if verdict.origin else "原处"
    there = turn_context._scene_name(module, verdict.target) if verdict.target else ""
    via = f"经「{verdict.exit}」" if verdict.exit else ""
    stay = f"；{_names(verdict.stayers)} 仍留在「{here}」" if verdict.stayers else ""
    s = verdict.status
    if s == MOVED:
        return (
            f"- {who} {via}已来到「{there}」（系统已更新位置）{stay}。按新位置叙述：{who} 眼前是"
            f"「{there}」的景象；留在原处的人只写他们所在之处能感知到的。不要再为此调用 scene_change。"
        )
    if s == PENDING:
        return (
            f"- {who} 想{via}前往「{there}」，但须先过「{verdict.skill}」检定：系统已暂存这次移动，"
            "检定成功才会移动、失败不动。本轮只写尝试与阻碍，不要提前描写新空间；"
            f"如尚未发起，请为 {who} 发起 dice_check（skill={verdict.skill}）。"
        )
    if s == "blocked":
        return (
            f"- {who} 想{via}通过，但{verdict.reason}：人仍在「{here}」。"
            "请明确描写阻碍，不要叙述其已通过。"
        )
    if s == "ambiguous":
        options = "；".join(verdict.clues) if verdict.clues else "此处不止一个去处"
        return (
            f"- {who} 要{via}离开，但对得上的出口不止一处（{options}）：人仍在「{here}」。"
            "请在叙事里自然地问清是哪一道——只用玩家已看到的方位/外观描述，不要说出他们还没去过的房间名。"
        )
    if s == "unreachable":
        return (
            f"- {who} 想去「{there}」，但{verdict.reason}：人仍在「{here}」。"
            "请说明为何到不了（或让其分步走过去），不要叙述其已抵达。"
        )
    if s == "unresolved":
        return (
            f"- {who} 想{via}离开「{here}」，但模组里找不到这个出口通往哪：人仍在原处。"
            "请回应这次尝试（那里是否真有出路），不要凭空把人送到别处。"
        )
    if s == "approach":
        return (
            f"- {who} 只是走近{verdict.exit or '出口'}查看，仍在「{here}」：回应这个动作"
            "（他在那儿看到、听到什么），但不要让其进入新空间。"
        )
    if s in ("discuss", "conditional", "negated"):
        label = {"discuss": "只是在商量/询问", "conditional": "说的是条件句，条件尚未满足",
                 "negated": "表态不进去"}[s]
        return f"- {who} {label}，人仍在「{here}」，本轮不移动。"
    return ""


def _render_notes(module: Module | None, verdicts: list[Verdict]) -> str:
    lines = [n for n in (_note(module, v) for v in verdicts) if n]
    if not lines:
        return ""
    return (
        "\n\n【本轮移动裁定——系统已按玩家原话与场景连通复核，位置以此为准】\n" + "\n".join(lines)
    )


def _verdict_from_record(record: dict, party: list[Character]) -> Verdict:
    by_id = {c.id: c for c in party}
    movers = [by_id[i] for i in record.get("movers") or [] if i in by_id]
    stayers = [c for c in party if c.name in (record.get("stayers") or [])]
    return Verdict(
        str(record.get("status") or ""), movers, record.get("origin"), record.get("target"),
        stayers, str(record.get("exit") or ""), str(record.get("skill") or ""),
        str(record.get("reason") or ""), list(record.get("clues") or []),
    )


def adjudicate_plan(
    db: Session, session_id: str, game_session: GameSession, module: Module | None,
    player_char: Character, teammates: list[Character] | None,
    plan, *, pre_gen_seq: int | None = None,
) -> tuple[list[str], str]:
    """裁定并执行本轮的移动提案。返回 (要广播的 chunk, 注入 KP 上下文的裁定说明)。

    先在内存里裁定全部提案，再**一笔事务**落下：所有落位与 move 事件、门控暂存、裁定记录。
    任一步失败整体回滚，记录也不留——重试时会重新裁定。
    幂等：同一回合锚点已裁定过（重新生成、生成后守卫再跑一遍）就复用记录、不再执行。
    """
    party = [player_char, *(teammates or [])]
    anchor = turn_anchor(db, session_id, pre_gen_seq)
    db.refresh(game_session)
    existing = turn_records(game_session, anchor)
    if existing:
        return [], _render_notes(module, [_verdict_from_record(r, party) for r in existing])
    proposals, legacy = _proposals(plan)
    if not proposals:
        return [], ""
    named_terms: set[str] = set()
    for sid in _location_ids(module):
        named_terms |= scene_terms(module, sid)
    stmts, stayed = turn_statements(db, session_id, party, pre_gen_seq, named_terms)
    legacy_gate = (
        (plan.check.skill or "").strip() if legacy and plan is not None and plan.requires_check else ""
    )
    verdicts: list[tuple[Verdict, bool]] = []
    busy: set[str] = set()
    for proposal in proposals:
        verdict = _adjudicate_one(
            db, session_id, game_session, module, proposal, party, stmts, stayed, busy,
            legacy_gate=legacy_gate,
        )
        if verdict is None or any(c.id in busy for c in verdict.movers):
            continue
        busy |= {c.id for c in verdict.movers}
        verdicts.append((verdict, bool(proposal.shared_check) and not legacy))
    if not verdicts:
        return [], ""

    staged: list[_Staged] = []
    records: list[dict] = []
    new_pending: list[dict] = []
    with effect_channel.transaction(db):
        for verdict, shared in verdicts:
            record = verdict.record(anchor)
            if verdict.status == MOVED:
                st = _stage_move(
                    db, session_id, game_session, module, verdict.movers, verdict.target, party,
                )
                if st is not None:
                    staged.append(st)
                record["char_status"] = {c.id: "moved" for c in verdict.movers}
            elif verdict.status == PENDING:
                chars = {c.id: "pending" for c in verdict.movers}
                record["char_status"] = dict(chars)
                record["shared"] = shared
                new_pending.append({
                    "anchor": anchor, "movers": [c.id for c in verdict.movers],
                    "origin": verdict.origin, "target": verdict.target, "skill": verdict.skill,
                    "exit": verdict.exit, "shared": shared, "chars": chars,
                })
            records.append(record)
            logger.info(
                "移动裁定：session=%s %s %s→%s status=%s %s",
                session_id, _names(verdict.movers), verdict.origin, verdict.target,
                verdict.status, verdict.reason,
            )
        taken = {cid for e in new_pending for cid in e["movers"]}
        old_pending = [
            e for e in (game_session.turn_state or {}).get(PENDING_KEY) or []
            if not (set(e.get("movers") or []) & taken)
        ]
        _set_turn_state(
            game_session, **{MOVES_KEY: records, PENDING_KEY: old_pending + new_pending},
        )
    db.refresh(game_session)
    return _announce(db, session_id, module, staged), _render_notes(
        module, [v for v, _shared in verdicts],
    )


def settle_pending_moves(
    db: Session, session_id: str, game_session: GameSession, module: Module | None,
    party: list[Character], *, skill: str, succeeded: bool, char_ids: set[str] | None = None,
) -> list[str]:
    """检定落地：**按人**结算门控移动。

    只认投骰者本人（``char_ids``）在条目里仍待投的那份：成功只移动他，失败只结束他的尝试，
    其他人照旧待投。唯一例外是规划器明确声明的共同障碍（``shared``）：一人成功即全员通过，
    一人失败仍只结束他自己。不知道谁掷的（``char_ids`` 为空）一律不结算。

    条目消费、落位、move 事件与裁定记录同一笔事务提交；失败全部回滚，提案原样保留，可重试。
    """
    if not char_ids:
        return []
    db.refresh(game_session)
    pending = [dict(e, chars=dict(e.get("chars") or {
        cid: "pending" for cid in e.get("movers") or []
    })) for e in (game_session.turn_state or {}).get(PENDING_KEY) or []]
    if not pending:
        return []
    records = [dict(r, char_status=dict(r.get("char_status") or {})) for r in turn_records(game_session)]
    by_id = {c.id: c for c in party}
    moves: list[tuple[str, list[Character]]] = []
    touched = False
    for entry in pending:
        if not check_gating.skill_matches(entry.get("skill", ""), skill):
            continue
        resolution = settle_check(
            entry["chars"], CheckOutcome(frozenset(char_ids), succeeded),
            shared=bool(entry.get("shared")),
            eligible_ids=frozenset(
                cid for cid in entry["chars"] if cid in by_id
                and session_service.get_char_location(game_session, cid) == entry.get("origin")
            ),
        )
        if not resolution.changed:
            continue
        touched = True
        states = dict(resolution.states)
        entry["chars"] = states
        rollers = resolution.roller_ids
        if resolution.mover_ids:
            moves.append((str(entry.get("target") or ""),
                          [by_id[cid] for cid in resolution.mover_ids]))
        for record in records:
            if record.get("anchor") == entry.get("anchor") and set(record.get("movers") or []) == set(
                entry.get("movers") or []
            ):
                record["char_status"].update(states)
                record["status"] = _rollup(record["char_status"])
        logger.info(
            "门控移动结算：session=%s %s %s → %s", session_id,
            "、".join(by_id[c].name for c in rollers if c in by_id), skill,
            "成功" if succeeded else "失败",
        )
    if not touched:
        return []
    remaining = [e for e in pending if any(st == "pending" for st in e["chars"].values())]
    staged: list[_Staged] = []
    with effect_channel.transaction(db):
        for target, movers in moves:
            st = _stage_move(db, session_id, game_session, module, movers, target, party)
            if st is not None:
                staged.append(st)
        _set_turn_state(game_session, **{PENDING_KEY: remaining, MOVES_KEY: records})
    db.refresh(game_session)
    return _announce(db, session_id, module, staged)


def pending_unissued(
    db: Session, session_id: str, game_session: GameSession, party: list[Character],
    pre_gen_seq: int,
) -> list[tuple[list[Character], str]]:
    """暂存了门控移动、本轮叙事后却还没挂出/掷出对应检定的人 → [(这些人, 技能)]。

    按人看：一人的检定已挂出或已掷，不代表其他人的也有了。共同障碍只需一人尝试，
    任一移动者已有检定就不再补挂。
    """
    from app.services import planned_effects   # 延迟导入：planned_effects 依赖本模块

    out = []
    by_id = {c.id: c for c in party}
    for entry in (game_session.turn_state or {}).get(PENDING_KEY) or []:
        states = entry.get("chars") or {cid: "pending" for cid in entry.get("movers") or []}
        waiting = [by_id[c] for c, st in states.items() if st == "pending" and c in by_id]
        if not waiting:
            continue
        issued = [
            m for m in waiting
            if planned_effects.requested_check_settled(db, session_id, m.id, pre_gen_seq, m.name)
        ]
        if entry.get("shared"):
            if not issued:
                out.append((waiting[:1], str(entry.get("skill") or "")))
            continue
        rest = [m for m in waiting if m not in issued]
        if rest:
            out.append((rest, str(entry.get("skill") or "")))
    return out


#: KP 的 scene_change 撞上这些裁定就拒绝：都是「玩家原话或通道本身说明人没动」。
#: no_evidence / unresolved 不拒——那只说明系统没找到证据，KP 手里可能有更多上下文。
_TOOL_REFUSE = frozenset({
    "discuss", "conditional", "negated", "approach", "ambiguous", "blocked", "failed",
    "unreachable",
})
_REFUSE_REASON = {
    "discuss": "玩家只是在商量", "conditional": "条件尚未满足", "negated": "玩家表态不进去",
    "approach": "玩家只是走近查看", "ambiguous": "去向不明确，需先问清是哪个出口",
    "blocked": "通道受阻", "failed": "检定失败，未能通过", "unreachable": "到不了那里",
}


def tool_guard(
    game_session: GameSession, target: str, movers: list[Character] | None,
    default_leader: Character | None = None,
) -> tuple[str, list[str] | None]:
    """KP 调 scene_change 时先看本轮裁定：返回 (拒绝理由, 沿用的移动者 id)。

    **按人**比对：这批人里有谁在同一目标上还待检定、或被裁定为不动（商量、受阻、去向不明、
    检定失败…）→ 拒绝；这批人在同一目标上都已落位 → 沿用他们（幂等）。没有相关裁定 → ("", None)。
    """
    if movers:
        mover_ids = {c.id for c in movers}
    else:
        mover_ids = {default_leader.id} if default_leader is not None else set()
    settled: list[str] = []
    for record in turn_records(game_session):
        ids = set(record.get("movers") or []) & mover_ids
        if not ids:
            continue
        same_target = record.get("target") == target
        statuses = {cid: char_status(record, cid) for cid in ids}
        if same_target and PENDING in statuses.values():
            return (
                f"这次移动须先过「{record.get('skill')}」检定，系统已暂存、谁成功谁才移动；"
                "现在不要切换场景。",
                None,
            )
        refused = [cid for cid, st in statuses.items() if st in _TOOL_REFUSE]
        if refused and (same_target or not record.get("target")):
            reason = record.get("reason") or _REFUSE_REASON.get(statuses[refused[0]], "本轮裁定未移动")
            names = "、".join(
                n for i, n in zip(record.get("movers") or [], record.get("mover_names") or [])
                if i in refused
            )
            return f"本轮移动裁定为不移动（{reason}）：{names} 仍在原处，未切换场景。", None
        if same_target:
            settled += [cid for cid, st in statuses.items() if st == MOVED]
    return "", (settled or None)


def busy_ids(game_session: GameSession) -> set[str]:
    """本轮已有移动裁定的人：默认跟随不再把他们卷进别的移动。"""
    return {i for r in turn_records(game_session) for i in (r.get("movers") or [])}


def resolve_tool_movers(raw: str, party: list[Character]) -> tuple[list[Character], list[str]]:
    """工具参数 movers → (认出的角色, 认不出的名字)。调用方据「认不出」拒绝，绝不静默回退成整队。"""
    names = [n for n in re.split(r"[、，,/\s]+", str(raw or "")) if n.strip()]
    found, unknown = [], []
    for name in names:
        hit = _match_chars([name], party)
        if hit:
            if hit[0] not in found:
                found.append(hit[0])
        else:
            unknown.append(name)
    return found, unknown


def default_movers(
    db: Session, session_id: str, game_session: GameSession, module: Module | None,
    target: str, party: list[Character], fallback_leader: Character | None,
) -> list[Character]:
    """KP 的 scene_change 没给 movers：按本轮原话推定谁在走——与规划器提案同一套跟随规则。

    带头人：本轮自己宣告移动、去向与 target 不冲突的人（真人、房主优先）；没人宣告时才回落到
    ``fallback_leader``（房主锚点，即旧语义「主角带队」）。独行（「抛下队友」「你们留在这」）、
    「我们一起」、劝人等一等、AI 默认跟随房主、不替沉默真人决定——都按带头人自己的原话判。
    没人宣告、也没有可回落的带头人 → 返回 []（调用方拒绝，而不是随手搬人）。
    """
    named_terms: set[str] = set()
    for sid in _location_ids(module):
        named_terms |= scene_terms(module, sid)
    stmts, stayed = turn_statements(db, session_id, party, None, named_terms)
    host = game_session.player_character_id
    candidates: list[Character] = []
    for c in party:
        stmt = stmts.get(c.id) or Statement()
        # 只有直接宣告移动（act）的人能带头；「我也去」「我跟上」是跟随，由下面的跟随规则处理。
        if c.id in stayed or stmt.kind != "act" or stmt.travel_scene:
            continue
        origin = session_service.get_char_location(game_session, c.id)
        dest = resolve_destination(module, origin, "。".join(stmt.sentences))
        if dest.status == "resolved" and dest.target != target:
            continue
        candidates.append(c)
    candidates.sort(key=lambda c: (
        not session_service.is_human_controlled(db, session_id, c.id), c.id != host,
    ))
    leader = candidates[0] if candidates else fallback_leader
    if leader is None:
        return []
    lead = stmts.get(leader.id) or Statement()
    origin = session_service.get_char_location(game_session, leader.id)
    followers = select_followers(
        db, session_id, game_session, leader, party, origin=origin, solo=lead.solo,
        collective=lead.collective, stmts=stmts, stayed=stayed,
        busy=busy_ids(game_session) - {leader.id}, module=module, target=target,
    )
    return [leader, *followers]
