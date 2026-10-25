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
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.application.movement_plans import movement_proposals as _proposals
from app.application.movement_adjudication import ActorSnapshot, Proposal, adjudicate_batch
from app.application.movement_execution import (
    MoveCommand, MovementStateUpdate, commit_movement_batch, prepare_movement_batch,
)
from app.application.movement_settlement import checks_to_issue, settle_pending_checks
from app.application.movement_space import project_scene, project_space
from app.application.movement_state import (
    MoveRecord, PendingMove, busy_ids as _busy_ids, load_move_records,
)
from app.domain import movement_party
from app.domain.movement_evidence import NON_MOVING as NON_MOVING
from app.domain.movement_language import Statement, analyze, has_any as _has
from app.domain.movement_space import Destination, resolve_destination as resolve_space_destination
from app.domain.movement import CheckOutcome
from app.models.character import Character
from app.models.module import Module
from app.models.session import GameSession
from app.services import (
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
        r.target == target and r.status_of(char.id) == MOVED for r in turn_moves(game_session)
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


@dataclass
class _MovementWriter:
    """现有数据库写入适配器；封装事务实现、存档键和 ORM 对象映射。"""
    db: Session
    session_id: str
    game_session: GameSession
    module: Module | None
    party: list[Character]
    extra_chars: list[Character] = field(default_factory=list)

    def transaction(self):
        return effect_channel.transaction(self.db)

    def stage(self, command: MoveCommand) -> _Staged | None:
        by_id = {c.id: c for c in [*self.party, *self.extra_chars]}
        return _stage_move(
            self.db, self.session_id, self.game_session, self.module,
            [by_id[cid] for cid in command.mover_ids], command.target, self.party,
            announce=command.announce,
        )

    def save_state(self, update: MovementStateUpdate) -> None:
        _set_turn_state(self.game_session, **{
            MOVES_KEY: [r.to_record() for r in update.records],
            PENDING_KEY: [e.to_record() for e in update.pending],
        })

    def refresh(self) -> None:
        self.db.refresh(self.game_session)


def stage_characters(
    db: Session, session_id: str, game_session: GameSession, module: Module | None,
    movers: list[Character], target: str, party: list[Character],
) -> None:
    """调用方事务内落位并固定留守者；不提交、不广播，由调用方保存自己的操作事件。

    用于人工 KP 等需要同时公开场景和写操作记录的入口。调用方必须持有事务并处理回滚。
    """
    _stage_move(db, session_id, game_session, module, movers, target, party, announce=False)


def move_characters(
    db: Session, session_id: str, game_session: GameSession, module: Module | None,
    movers: list[Character], target: str, party: list[Character], *, announce: bool = True,
) -> list[str]:
    """把 movers 移到 target：本函数拥有事务，一次提交；返回提交后要广播的 chunk。"""
    db.refresh(game_session)
    staged = commit_movement_batch(
        _MovementWriter(db, session_id, game_session, module, party, movers),
        (MoveCommand(target, tuple(c.id for c in movers), announce),),
    )
    return _announce(db, session_id, module, list(staged))


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
    snapshots = tuple(
        _participant(db, session_id, game_session, c, stmts.get(c.id) or Statement(),
                     module=module, resolve_from=origin, resolve_target=target)
        for c in party
    )
    ids = movement_party.select_followers(
        snapshots, leader_id=leader.id, host_id=game_session.player_character_id,
        origin=origin, solo=solo, collective=collective, target=target,
        stayed=frozenset(stayed), busy=frozenset(busy or ()),
        named=frozenset(c.id for c in (named or [])),
    )
    by_id = {c.id: c for c in party}
    return [by_id[cid] for cid in ids]


def _participant(
    db: Session, session_id: str, game_session: GameSession, char: Character, stmt: Statement,
    *, module: Module | None, resolve_from: str | None, resolve_target: str | None,
) -> movement_party.Participant:
    """只在服务边界读取 ORM 与控制权，领域选择只消费值快照。"""
    destination = None
    if module is not None and stmt.kind == "act" and not stmt.travel_scene and resolve_target:
        result = resolve_destination(module, resolve_from, "。".join(stmt.sentences))
        if result.status == "resolved":
            destination = result.target
    return movement_party.Participant(
        id=char.id, location=session_service.get_char_location(game_session, char.id),
        human=session_service.is_human_controlled(db, session_id, char.id),
        stance=stmt.kind, solo=stmt.solo, hesitant=stmt.hesitant,
        travel_scene=stmt.travel_scene, destination=destination, collective=stmt.collective,
    )


# ── 回合裁定记录 ─────────────────────────────────────────────────────

def turn_records(game_session: GameSession, anchor: int | None = None) -> list[dict]:
    """兼容入口：原始裁定记录字典。本模块内的规则一律经 ``turn_moves`` 读快照。"""
    records = list((game_session.turn_state or {}).get(MOVES_KEY) or [])
    if anchor is None:
        return records
    return [r for r in records if r.get("anchor") == anchor]


def turn_moves(game_session: GameSession, anchor: int | None = None) -> tuple[MoveRecord, ...]:
    return load_move_records((game_session.turn_state or {}).get(MOVES_KEY), anchor)


def _pending_moves(game_session: GameSession) -> tuple[PendingMove, ...]:
    return tuple(
        PendingMove.from_record(e) for e in (game_session.turn_state or {}).get(PENDING_KEY) or ()
    )


def _set_turn_state(game_session: GameSession, **updates) -> None:
    """在调用方事务里改 turn_state（不提交）。单独成函数，也是测试注入写入失败的接缝。"""
    ts = dict(game_session.turn_state or {})
    ts.update(updates)
    game_session.turn_state = ts


def char_status(record: dict, char_id: str) -> str:
    """某人在一条裁定里的状态：门控移动按人记（pending/tried/moved/failed），其余沿用整条的状态。

    tried＝共同障碍上他的尝试失败了，但障碍还可能被别人解除——对外仍是「待检定」，不是「失败」。
    兼容入口，解释逻辑在 ``MoveRecord.status_of``。
    """
    return MoveRecord.from_record(record).status_of(char_id)


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


@dataclass
class _MovementNavigation:
    module: Module | None
    game_session: GameSession

    def resolve(self, origin: str | None, text: str, exit_hint: str = "") -> Destination:
        return resolve_destination(self.module, origin, text, exit_hint)

    def reachable(self, origin: str | None, target: str) -> bool:
        return session_service.find_scene_path(
            self.module, origin, target,
            via_allowed=session_service.passable_scene_ids(self.game_session),
        ) is not None

    def obstruction_reason(self, origin: str | None, target: str) -> str:
        blocker = session_service.travel_blocker(self.module, self.game_session, origin, target)
        return (
            f"要先经过{turn_context._scene_name(self.module, blocker[0])}（{blocker[1]}）" if blocker
            else "与当前位置不连通"
        )


def _actor_snapshots(
    db: Session, session_id: str, game_session: GameSession,
    party: list[Character], stmts: dict[str, Statement],
) -> tuple[ActorSnapshot, ...]:
    snapshots = []
    for char in party:
        stmt = stmts.get(char.id) or Statement()
        snapshots.append(ActorSnapshot(movement_party.Participant(
            id=char.id, location=session_service.get_char_location(game_session, char.id),
            human=session_service.is_human_controlled(db, session_id, char.id),
            stance=stmt.kind, solo=stmt.solo, hesitant=stmt.hesitant,
            travel_scene=stmt.travel_scene, collective=stmt.collective,
        ), tuple(stmt.sentences)))
    return tuple(snapshots)


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


def _verdict_from_record(record: MoveRecord, party: list[Character]) -> Verdict:
    """重新生成时从已存裁定恢复说明：只把记录里的 id/名字映射回本轮的角色对象。"""
    by_id = {c.id: c for c in party}
    movers = [by_id[i] for i in record.mover_ids if i in by_id]
    stayers = [c for c in party if c.name in record.stayer_names]
    return Verdict(
        record.status, movers, record.origin, record.target, stayers, record.exit,
        record.skill, record.reason, list(record.clues),
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
    existing = turn_moves(game_session, anchor)
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
    decisions = adjudicate_batch(
        tuple(Proposal(p, tuple(c.id for c in _match_chars(list(p.actors), party))) for p in proposals),
        _actor_snapshots(db, session_id, game_session, party, stmts),
        _MovementNavigation(module, game_session), host_id=game_session.player_character_id,
        stayed=frozenset(stayed), legacy=legacy, legacy_gate=legacy_gate,
    )
    by_id = {c.id: c for c in party}
    verdicts = [(Verdict(
        d.status, [by_id[cid] for cid in d.mover_ids], d.origin, d.target,
        [by_id[cid] for cid in d.stayer_ids], d.exit, d.skill, d.reason, list(d.clues),
    ), shared) for d, shared in decisions]
    if not verdicts:
        return [], ""

    prepared = prepare_movement_batch(
        decisions, anchor=anchor, names={c.id: c.name for c in party}, pending=_pending_moves(game_session),
    )
    staged = commit_movement_batch(
        _MovementWriter(db, session_id, game_session, module, party), prepared.commands,
        state=prepared.state,
    )
    for verdict, _shared in verdicts:
        logger.info(
            "移动裁定：session=%s %s %s→%s status=%s %s",
            session_id, _names(verdict.movers), verdict.origin, verdict.target,
            verdict.status, verdict.reason,
        )
    return _announce(db, session_id, module, list(staged)), _render_notes(
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
    pending = _pending_moves(game_session)
    if not pending:
        return []
    settlement = settle_pending_checks(
        pending, turn_moves(game_session), skill=skill,
        outcome=CheckOutcome(frozenset(char_ids), succeeded),
        locations={c.id: session_service.get_char_location(game_session, c.id) for c in party},
    )
    if settlement is None:
        return []
    staged = commit_movement_batch(
        _MovementWriter(db, session_id, game_session, module, party), settlement.commands,
        state=settlement.state,
    )
    names = {c.id: c.name for c in party}
    for rollers in settlement.rollers:
        logger.info(
            "门控移动结算：session=%s %s %s → %s", session_id,
            "、".join(names[c] for c in rollers if c in names), skill, "成功" if succeeded else "失败",
        )
    return _announce(db, session_id, module, list(staged))


def pending_unissued(
    game_session: GameSession, party: list[Character], issued: Callable[[Character], bool],
) -> list[tuple[list[Character], str]]:
    """暂存了门控移动、本轮叙事后却还没挂出/掷出对应检定的人 → [(这些人, 技能)]。

    ``issued`` 由调用方提供：本轮是否已为该角色挂出或掷出检定（事件查询归检定与计划效果一侧，
    本模块不反向依赖它）。按人与共同障碍的补挂规则见 ``checks_to_issue``。
    """
    by_id = {c.id: c for c in party}
    pending = _pending_moves(game_session)
    waiting = {cid for entry in pending for cid in entry.waiting_ids if cid in by_id}
    plan = checks_to_issue(
        pending, party_ids=frozenset(by_id),
        issued_ids=frozenset(cid for cid in waiting if issued(by_id[cid])),
    )
    return [([by_id[cid] for cid in ids], skill) for ids, skill in plan]


def has_pending_moves(game_session: GameSession) -> bool:
    """是否还有等骰子落地的门控移动；其他模块据此决定要不要走结算，不直接读存档键。"""
    return bool(_pending_moves(game_session))


def has_verdict_for(game_session: GameSession, target: str) -> bool:
    """本轮是否已就这个目的地给过移动裁定（落位、待检定、受阻……都算）。"""
    return any(r.target == target for r in turn_moves(game_session))


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
    for record in turn_moves(game_session):
        ids = set(record.mover_ids) & mover_ids
        if not ids:
            continue
        same_target = record.target == target
        statuses = {cid: record.status_of(cid) for cid in ids}
        if same_target and PENDING in statuses.values():
            return (
                f"这次移动须先过「{record.skill}」检定，系统已暂存、谁成功谁才移动；"
                "现在不要切换场景。",
                None,
            )
        refused = [cid for cid, st in statuses.items() if st in _TOOL_REFUSE]
        if refused and (same_target or not record.target):
            reason = record.reason or _REFUSE_REASON.get(statuses[refused[0]], "本轮裁定未移动")
            names = "、".join(record.names_of(refused))
            return f"本轮移动裁定为不移动（{reason}）：{names} 仍在原处，未切换场景。", None
        if same_target:
            settled += [cid for cid, st in statuses.items() if st == MOVED]
    return "", (settled or None)


def busy_ids(game_session: GameSession) -> set[str]:
    """本轮已有移动裁定的人：默认跟随不再把他们卷进别的移动。"""
    return _busy_ids(turn_moves(game_session))


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
    snapshots = tuple(
        _participant(
            db, session_id, game_session, c, stmts.get(c.id) or Statement(), module=module,
            resolve_from=session_service.get_char_location(game_session, c.id), resolve_target=target,
        )
        for c in party
    )
    leader_id = movement_party.default_leader(
        snapshots, target=target, host_id=game_session.player_character_id,
        stayed=frozenset(stayed), fallback_id=fallback_leader.id if fallback_leader else None,
    )
    if leader_id is None:
        return []
    by_id = {c.id: c for c in party}
    leader = by_id.get(leader_id) or fallback_leader
    lead = stmts.get(leader_id) or Statement()
    origin = session_service.get_char_location(game_session, leader_id)
    ids = movement_party.select_followers(
        snapshots, leader_id=leader_id, host_id=game_session.player_character_id,
        origin=origin, solo=lead.solo, collective=lead.collective, target=target,
        stayed=frozenset(stayed), busy=frozenset(busy_ids(game_session) - {leader_id}),
    )
    return [leader, *[by_id[cid] for cid in ids]]
