from __future__ import annotations

import secrets

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.character import Character
from app.models.event_log import EventLog
from app.models.module import Module
from app.models.session import GameSession
from app.models.session_navigation import SessionNavigation
from app.models.session_participant import SessionParticipant


# ── 拆出的职责簇：本模块保留同名 re-export，旧导入路径继续可用 ──────────────
from app.services.event_store import (  # noqa: F401
    KP_ONLY_SENTINEL,
    SEARCHABLE_EVENT_TYPES,
    SNIPPET_CHARS,
    SNIPPET_LEAD,
    add_event,
    delete_pending_event,
    get_latest_events,
    get_next_sequence_num,
    get_session_events,
    is_kp_only_event,
    search_events,
    search_snippet,
    set_event_group,
    update_pending_event,
)
from app.services.navigation_service import (  # noqa: F401
    STAY_META_KEY,
    derive_scene_keywords,
    find_scene_path,
    get_char_location,
    get_party_locations,
    get_visited_scenes,
    known_scene_ids,
    list_known_locations,
    list_visible_map_nodes,
    passable_scene_ids,
    scene_neighbors,
    scene_unlock_keywords,
    set_char_location,
    stayed_char_ids,
    travel_blocker,
    update_scene,
    visited_scene_ids,
)
from app.services.turn_state_service import (  # noqa: F401
    add_pending_check,
    append_pending_batch_result,
    append_pending_group_check_result,
    commit_turn,
    find_pending_check,
    find_pending_san_check,
    get_pending_check,
    human_character_ids,
    pop_pending_check,
    rollback_last_kp_output,
    set_turn_confirm,
    turn_confirm_state,
)


# 房间码字母表：去掉 I/O/0/1（手抄易混）。8 位 → 32^8 ≈ 1.1e12，约 40 bit。
# 旧版是 uuid4().hex[:6]，只有 16 个字符、24 bit——配合当时没有的限流，
# 在线枚举是可行的。已发出的旧房间码继续有效（查询按精确匹配）。
_ROOM_CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
_ROOM_CODE_LEN = 8


def _gen_room_code(db: Session) -> str:
    for _ in range(20):
        code = "".join(secrets.choice(_ROOM_CODE_ALPHABET) for _ in range(_ROOM_CODE_LEN))
        if not db.query(GameSession).filter(GameSession.room_code == code).first():
            return code
    # 撞了 20 次说明库里房间多到不正常，加长而不是放弃随机性
    return "".join(secrets.choice(_ROOM_CODE_ALPHABET) for _ in range(_ROOM_CODE_LEN + 4))


def active_character_ids(
    db: Session, exclude_session_id: str | None = None
) -> set[str]:
    """返回当前所有活跃/暂停会话占用的角色 id（含主角与 AI 队友）。

    既读旧的 ``player_character_id`` 快捷字段，也读 ``session_participants``，
    供开局冲突校验和 ``/characters?available=true`` 对齐使用。

    ``setup``（大厅里等开局）同样算占用：建局改成恒进大厅之后，一个角色可能长时间停在
    「已入座、还没开局」的状态。只认 active 的话，同一个角色能被同时拉进两个房间，
    等两边都开局就撞车了——而这个坑正是本函数存在的理由。
    """
    q = db.query(GameSession).filter(GameSession.status.in_(["setup", "active", "paused"]))
    if exclude_session_id:
        q = q.filter(GameSession.id != exclude_session_id)
    sessions = q.all()
    session_ids = [s.id for s in sessions]
    parts = (
        db.query(SessionParticipant)
        .filter(SessionParticipant.session_id.in_(session_ids))
        .all()
    ) if session_ids else []
    ids = {p.character_id for p in parts if p.character_id}
    # ``player_character_id`` 只作为**没有席位记录**的旧会话回落。只要存在
    # participant 行，就以席位为准——主角席换人后若遗留旧值，会在这里把
    # 已经没人使用的旧角色继续算作「占用中」，表现为角色管理可见、大厅候选不可见。
    sessions_with_parts = {p.session_id for p in parts}
    ids |= {
        s.player_character_id
        for s in sessions
        if s.id not in sessions_with_parts and s.player_character_id
    }
    return ids


def _normalize_participants(
    participants: list[dict], *,
    allow_empty_primary: bool = False,
    allow_ai_primary: bool = False,
) -> list[dict]:
    """补全主角标记，去重保序。

    真人 KP 新建房间时，主角席可以暂时为空，等待另一枚 token 认领；旧模式仍要求
    主角席带角色，避免旧客户端误建出无法行动的房间。
    ``allow_ai_primary``（真人 KP 新模型）：玩家席可全 AI，主角只是默认上下文锚点，
    允许落在 AI 席上（玩家角色一视同仁）；其余模式主角仍强制真人。
    """
    seen: set[str] = set()
    seats: list[dict] = []
    for p in participants:
        cid = p.get("character_id")
        role = p.get("role", "ai")
        if role not in ("human", "ai"):
            raise ValueError("玩家席位 role 只能是 human 或 ai")
        if cid:
            if cid in seen:
                raise ValueError("同一角色不能在同一会话中占据多个席位")
            seen.add(cid)
        seats.append(
            {
                "character_id": cid,
                "role": role,
                "is_primary": bool(p.get("is_primary", False)),
            }
        )
    # 空席（无角色）只能是 human 席
    for s in seats:
        if not s["character_id"]:
            s["role"] = "human"

    primaries = [s for s in seats if s["is_primary"]]
    if not primaries:
        # 取第一个有角色的席位作主角
        filled = [s for s in seats if s["character_id"]]
        if not filled and allow_empty_primary and seats:
            seats[0]["is_primary"] = True
            primaries = [seats[0]]
        elif not filled:
            raise ValueError("必须至少有一个已填角色的主角席位")
        else:
            filled[0]["is_primary"] = True
            primaries = [filled[0]]
    elif len(primaries) > 1:
        raise ValueError("只能有一个主角席位")
    if not primaries[0]["character_id"] and not allow_empty_primary:
        raise ValueError("主角席位必须填入角色")
    # 主角必为真人（真人 KP 新模型除外：全 AI 玩家席时主角可以是 AI 锚点）
    if not (allow_ai_primary and primaries[0]["character_id"]):
        primaries[0]["role"] = "human"
    return seats


def create_session(
    db: Session,
    module_id: str,
    participants: list[dict],
    creator_token: str | None = None,
    kp_mode: str = "ai",
    *,
    commit: bool = True,
) -> GameSession:
    module = db.get(Module, module_id)
    if not module:
        raise ValueError("模组不存在")
    if not participants:
        raise ValueError("必须至少提供一个主角席位")
    if kp_mode not in ("ai", "human"):
        raise ValueError("kp_mode 必须是 ai 或 human")

    # 旧客户端在真人 KP 模式下仍会把创建者角色提交到主角席；保留这类已存在的
    # 调用为 legacy identity，避免升级后剥夺旧房间的双席位权限。新客户端传空主角席，
    # 才启用严格的 KP/玩家分离模型。
    requested_primary = next(
        (p for p in participants if p.get("is_primary")), participants[0]
    )
    # 旧客户端的特征是「创建者自己的真人角色占主角席」；新模型下主角席带 AI 角色
    # （全 AI 玩家席）不算 legacy——否则会误建出旧双席位模型。
    legacy_human_kp = (
        kp_mode == "human"
        and bool(requested_primary.get("character_id"))
        and (requested_primary.get("role") or "ai") == "human"
    )
    new_human_kp = kp_mode == "human" and not legacy_human_kp
    # 建房时一律允许空主角席：「模组是房间的身份（建房时定），座位是房间的内容（房间里配）」——
    # 创建者在上一屏只选了本子与 KP 模式，角色是进大厅之后才挑的。
    # 从前只对真人 KP 放开，因为 AI KP 模式是「在建局表单里一次配完」，那条路已经不存在了。
    # 开局门槛不受影响：空席仍会被 lobby_gaps 拦住（见其实现），不会漏出「没有角色的局」。
    seats = _normalize_participants(
        participants,
        allow_empty_primary=True,
        allow_ai_primary=new_human_kp,
    )

    for seat in seats:
        if seat["character_id"] and not db.get(Character, seat["character_id"]):
            raise ValueError("角色不存在")

    occupied = active_character_ids(db)
    clash = [
        s["character_id"] for s in seats
        if s["character_id"] and s["character_id"] in occupied
    ]
    if clash:
        raise ValueError("所选角色正在进行其他游戏，请先完成或结束当前游戏")

    primary = next(s for s in seats if s["is_primary"])
    primary_id = primary["character_id"]

    first_scene_id = None
    if module.scenes:
        first_scene_id = module.scenes[0].get("id")

    # **建局恒为建房**：一律落在 setup，由大厅统一开局。
    #
    # 从前这里分了岔——没有空的真人席（单人或队友全是 AI）就跳过大厅直接 active，
    # 为的是「保持原快速开局体验」。代价有三，都比省下的那一次点击贵：
    # ① 心智不一致，用户得记住「什么情况下会进大厅」；
    # ② 建完就定死了，配错一个角色只能删档重来——大厅里本来可以换人、踢座、改配置；
    # ③ 想让朋友后来加入，得整局重开（大厅里点一下「设为真人空席」就行）。
    # 单人多出的那一次点击只是一次：房主席在建局时就置为 ready（见下方 ready 的算法），
    # 所以全 AI / 单人局落进大厅时门槛已经是满的，点「开始冒险」即走。
    status = "setup"

    identity_version = 1 if legacy_human_kp else 2
    game_session = GameSession(
        module_id=module_id,
        player_character_id=primary_id,
        status=status,
        kp_mode=kp_mode,
        host_token=None if legacy_human_kp else creator_token,
        identity_version=identity_version,
        room_code=_gen_room_code(db),
        current_scene_id=first_scene_id,
    )
    for order, seat in enumerate(seats):
        claimed = bool(seat["character_id"])
        # 真人 KP 新模型中创建者只占 KP 席；AI KP/旧兼容模型的创建者占主角席。
        owner = (
            creator_token
            if seat["is_primary"] and not (kp_mode == "human" and not legacy_human_kp)
            else None
        )
        # AI 席与房主席默认就绪；空/待认领的真人席需手动准备。
        # 房主席默认就绪是「建局恒进大厅」之后单人局仍然顺手的关键：他刚在上一屏选完自己的
        # 角色，再要求他跟自己确认一次「准备好了」纯属仪式。后来加入的真人仍要自己点。
        ready = seat["role"] == "ai" or (seat["is_primary"] and claimed)
        game_session.participants.append(
            SessionParticipant(
                character_id=seat["character_id"],
                role=seat["role"],
                is_primary=seat["is_primary"],
                seat_order=order,
                claimed=claimed,
                owner_token=owner,
                ready=ready,
                identity_version=identity_version,
            )
        )
    if kp_mode == "human":
        # 新模型中创建者只拥有 KP 席；legacy 请求保留旧双席位以兼容已有客户端。
        game_session.participants.append(
            SessionParticipant(
                character_id=None,
                role="kp",
                is_primary=False,
                seat_order=len(seats),
                owner_token=creator_token,
                claimed=bool(creator_token),
                ready=True,
                identity_version=identity_version,
            )
        )
    db.add(game_session)
    if first_scene_id:
        # 初始 visited_scenes 落在导航表：先 flush 让 game_session.id 落库
        # （navigation 是共享主键，session_id 即主键），再补一行。
        db.flush()
        db.add(SessionNavigation(session_id=game_session.id, visited_scenes=[first_scene_id]))
    # 创建者的主角绑定到其 token
    if creator_token and primary_id and not (kp_mode == "human" and not legacy_human_kp):
        char = db.get(Character, primary_id)
        if char and not char.owner_token:
            char.owner_token = creator_token
    if commit:
        db.commit()
        db.refresh(game_session)
    else:
        db.flush()
    return game_session


def get_session_by_code(db: Session, room_code: str) -> GameSession | None:
    return (
        db.query(GameSession)
        .filter(GameSession.room_code == room_code.upper())
        .first()
    )


def join_session(db: Session, session_id: str, token: str) -> GameSession:
    """进入大厅即预留一个真人席，使房间立即出现在该 token 的游戏列表中。"""
    if not token:
        raise ValueError("缺少玩家身份")
    session = db.get(GameSession, session_id)
    if not session:
        raise ValueError("房间不存在")
    if session.status != "setup":
        raise ValueError("游戏已经开始，无法加入大厅")

    existing = (
        db.query(SessionParticipant)
        .filter(
            SessionParticipant.session_id == session_id,
            SessionParticipant.owner_token == token,
        )
        .first()
    )
    if existing:
        return session

    seat = (
        db.query(SessionParticipant)
        .filter(
            SessionParticipant.session_id == session_id,
            SessionParticipant.role == "human",
            SessionParticipant.character_id.is_(None),
            SessionParticipant.owner_token.is_(None),
            SessionParticipant.claimed.is_(False),
        )
        .order_by(SessionParticipant.seat_order.asc())
        .first()
    )
    if not seat:
        raise ValueError("房间没有可加入的真人席位")

    seat.owner_token = token
    seat.claimed = True
    seat.ready = False
    if session.identity_version >= 2:
        seat.identity_version = 2
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if "uq_session_participant_token_v2" in str(exc).lower():
            raise ValueError("同一个 token 在本房间只能占用一个席位") from exc
        raise
    db.refresh(session)
    return session


def claim_seat(
    db: Session, session_id: str, seat_order: int, character_id: str | None, token: str,
) -> GameSession:
    """用 token 认领一个空席：human 席绑定角色，KP 席不绑定角色。"""
    if not token:
        raise ValueError("缺少玩家身份")
    session = db.get(GameSession, session_id)
    if not session:
        raise ValueError("房间不存在")

    seat = (
        db.query(SessionParticipant)
        .filter(
            SessionParticipant.session_id == session_id,
            SessionParticipant.seat_order == seat_order,
        )
        .first()
    )
    if not seat:
        raise ValueError("席位不存在")
    if seat.role not in ("human", "kp"):
        raise ValueError("只能认领真人或 KP 席位")
    # 自己的席位可以再动：还没选角色时是「补选」，已选了则是「换人」。
    # 换人只在开局前允许——开局后消息、骰点与战斗状态都绑着角色，中途换掉会留下
    # 一堆指向旧角色的记录，那不是换角色而是把存档搞坏。
    mine = seat.role == "human" and seat.claimed and seat.owner_token == token
    reserved_by_me = bool(mine and (not seat.character_id or session.status == "setup"))
    if seat.claimed and not reserved_by_me:
        raise ValueError("已开局，无法更换角色" if mine else "该席位已被认领")

    strict_identity = session.host_token is not None or seat.identity_version >= 2
    if strict_identity:
        already_owned = (
            db.query(SessionParticipant)
            .filter(
                SessionParticipant.session_id == session_id,
                SessionParticipant.owner_token == token,
                SessionParticipant.identity_version >= 2,
                SessionParticipant.id != seat.id,
            )
            .first()
        )
        if already_owned:
            raise ValueError("同一个 token 在本房间只能占用一个席位")

    if seat.role == "kp":
        seat.owner_token = token
        seat.claimed = True
        seat.ready = True
        seat.identity_version = 2 if strict_identity else seat.identity_version
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            if "uq_session_participant_token_v2" in str(exc).lower():
                raise ValueError("同一个 token 在本房间只能占用一个席位") from exc
            raise
        db.refresh(session)
        return session

    if not character_id:
        raise ValueError("真人玩家席位必须选择角色")

    char = db.get(Character, character_id)
    if not char:
        raise ValueError("角色不存在")
    if char.owner_token and char.owner_token != token:
        raise ValueError("该角色属于其他玩家")

    occupied = active_character_ids(db)
    if character_id in occupied:
        raise ValueError("该角色正在进行其他游戏")

    seat.character_id = character_id
    seat.owner_token = token
    seat.claimed = True
    # 换了角色就得重新确认准备——否则会带着「已准备」的状态换成另一个人。
    # 首次认领时它本来就是 False，这里无副作用。
    seat.ready = False
    if strict_identity:
        seat.identity_version = 2
    char.owner_token = token
    # 真人坐上去了，这张卡就是一张玩家调查员卡——哪怕它当初是按「AI 队友」生成的。
    #
    # is_player 决定的只是**归档口径**（结局后只给玩家角色写模组经历）和**它出现在
    # 哪个候选池**里；谁来驱动这个角色看的是席位的 role，与这个标志无关。不在这里
    # 转正的话，玩家认领完一张队友卡，下次在「我的角色」里就再也找不到它，结局也
    # 不给它归档——卡还是那张卡，只因为当初点的是哪个按钮。
    char.is_player = True
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if "uq_session_participant_token_v2" in str(exc).lower():
            raise ValueError("同一个 token 在本房间只能占用一个席位") from exc
        raise
    db.refresh(session)
    if seat.is_primary:
        # 主角席换了角色，player_character_id 必须跟着换——它既用于旧会话回落，
        # 也参与 active_character_ids 的占用计算。漏掉这一步会让旧角色被
        # 「幽灵占用」：席位里明明没人用了，候选池里却永远看不到它。
        _reseat_primary(db, session)
    return session


def get_participants(db: Session, session_id: str) -> list[SessionParticipant]:
    return (
        db.query(SessionParticipant)
        .filter(SessionParticipant.session_id == session_id)
        .order_by(SessionParticipant.seat_order.asc())
        .all()
    )


def _primary_seat(db: Session, session_id: str) -> SessionParticipant | None:
    return (
        db.query(SessionParticipant)
        .filter(
            SessionParticipant.session_id == session_id,
            SessionParticipant.is_primary.is_(True),
        )
        .first()
    )


def is_host(db: Session, session_id: str, token: str | None) -> bool:
    """房主身份独立保存；旧房间回落到主角席 owner_token。"""
    session = db.get(GameSession, session_id)
    if session and session.host_token is not None:
        return bool(token and token == session.host_token)
    seat = _primary_seat(db, session_id)
    return bool(token and seat and seat.owner_token == token)


def is_kp(db: Session, session_id: str, token: str | None) -> bool:
    """真人 KP 授权：只认 role=kp 的席位，不把普通房主权限隐式升级为 KP。"""
    if not token:
        return False
    seat = (
        db.query(SessionParticipant)
        .filter(
            SessionParticipant.session_id == session_id,
            SessionParticipant.role == "kp",
            SessionParticipant.owner_token == token,
        )
        .first()
    )
    return seat is not None


def authorize_kp(db: Session, session_id: str, token: str | None) -> GameSession:
    """返回可由真人 KP 操作的会话；失败统一抛出业务错误供 API 转换。"""
    session = db.get(GameSession, session_id)
    if session is None:
        raise ValueError("房间不存在")
    if session.kp_mode != "human":
        raise ValueError("该会话未启用真人 KP 模式")
    if not is_kp(db, session_id, token):
        raise ValueError("只有真人 KP 席位可以执行该操作")
    if session.status != "active":
        raise ValueError("会话未处于活跃状态")
    return session


# ── 结束模组：全体非AI玩家共识投票 ──────────────────────────────────
# 结束不再由房主单方拍板，而是「所有真人玩家一致同意」才执行（单人时自然退化为一键结束）。
# 投票者口径与回合确认（turn_confirm）一致：所有已填角色的真人席，按 character_id 计票；
# 投票经 resolve_actor 按 token 校验席位归属。按 A 方案不做掉线豁免（严格全体一致）。

def _end_voter_ids(db: Session, session_id: str) -> set[str]:
    """有资格参与结束投票的角色 id = 所有已填角色的真人席（与回合确认口径一致）。"""
    return {
        p.character_id for p in get_participants(db, session_id)
        if p.role == "human" and p.character_id
    }


def end_vote_public(db: Session, session_id: str) -> dict:
    """对外可见的结束投票态：每个真人玩家是否已同意、已同意数 / 总数、是否进行中。"""
    session = db.get(GameSession, session_id)
    voter_ids = _end_voter_ids(db, session_id)
    agreed = (
        set((session.world_state or {}).get("end_vote", {}).get("agreed") or []) & voter_ids
        if session else set()
    )
    names = {
        c.id: c.name
        for c in db.query(Character).filter(Character.id.in_(voter_ids)).all()
    } if voter_ids else {}
    voters = [
        {"character_id": cid, "name": names.get(cid, "玩家"), "agreed": cid in agreed}
        for cid in sorted(voter_ids)
    ]
    return {
        "open": len(agreed) > 0,
        "voters": voters,
        "agreed_count": len(agreed),
        "total": len(voter_ids),
    }


def cast_end_vote(
    db: Session, session_id: str, token: str | None, acting_character_id: str | None,
) -> tuple[bool, dict]:
    """兼容入口：校验 token 后由纯业务投票函数执行。"""
    actor = resolve_actor(db, session_id, token, acting_character_id)
    return cast_end_vote_for_actor(db, session_id, actor.id)


def cast_end_vote_for_actor(
    db: Session,
    session_id: str,
    actor_id: str,
) -> tuple[bool, dict]:
    """以已授权真人角色投票；返回 (是否结束, 公开投票态)。"""
    from app.services import world_state

    voter_ids = _end_voter_ids(db, session_id)
    if actor_id not in voter_ids:
        raise ValueError("只有真人玩家可参与结束投票")
    session = db.get(GameSession, session_id)
    ev = dict((session.world_state or {}).get("end_vote") or {})
    agreed = (set(ev.get("agreed") or []) | {actor_id}) & voter_ids
    world_state.set_key(db, session, "end_vote", {"agreed": sorted(agreed)})
    if agreed >= voter_ids:                                            # 全体真人一致同意
        update_session_status(db, session_id, "ended")
        world_state.set_key(db, db.get(GameSession, session_id), "end_vote", None)
        return True, end_vote_public(db, session_id)
    return False, end_vote_public(db, session_id)


def cancel_end_vote(
    db: Session, session_id: str, token: str | None, acting_character_id: str | None,
) -> dict:
    """兼容入口：校验 token 后由纯业务撤票函数执行。"""
    actor = resolve_actor(db, session_id, token, acting_character_id)
    return cancel_end_vote_for_actor(db, session_id, actor.id)


def cancel_end_vote_for_actor(
    db: Session,
    session_id: str,
    actor_id: str,
) -> dict:
    """由已授权真人角色撤销进行中的结束投票。"""
    from app.services import world_state

    if actor_id not in _end_voter_ids(db, session_id):
        raise ValueError("只有真人玩家可撤销结束投票")
    world_state.set_key(db, db.get(GameSession, session_id), "end_vote", None)
    return end_vote_public(db, session_id)


def set_ready(
    db: Session, session_id: str, token: str | None, ready: bool
) -> GameSession:
    """把当前 token 拥有的席位的准备态置位。"""
    session = db.get(GameSession, session_id)
    if not session:
        raise ValueError("房间不存在")
    seat = (
        db.query(SessionParticipant)
        .filter(
            SessionParticipant.session_id == session_id,
            SessionParticipant.owner_token == token,
            SessionParticipant.role == "human",
        )
        .order_by(SessionParticipant.seat_order.asc())
        .first()
    )
    if not token or not seat:
        raise ValueError("你不在该房间中")
    seat.ready = bool(ready)
    db.commit()
    db.refresh(session)
    return session


def lobby_gaps(db: Session, session_id: str) -> list[str]:
    """返回开局门槛缺口；空列表代表满足开局条件。"""
    session = db.get(GameSession, session_id)
    parts = get_participants(db, session_id)
    gaps: list[str] = []
    # KP 席位是无角色的控制席，不能被当作待认领的玩家席位。
    player_parts = [p for p in parts if p.role != "kp"]
    # 新真人 KP 房间允许预留多个真人空席，但 AI 席必须在开局前填入角色。
    # 这使创建者不需要伪造一个玩家角色，也不会因为预留席位阻塞开局。
    if session and session.kp_mode == "human" and session.identity_version >= 2:
        empty = [p for p in player_parts if p.role == "ai" and not p.character_id]
    else:
        empty = [p for p in player_parts if not p.character_id]
    if empty:
        gaps.append(f"还有 {len(empty)} 个空席未填角色")
    not_ready = [
        p for p in parts if p.character_id and p.role == "human" and not p.ready
    ]
    if not_ready:
        gaps.append(f"还有 {len(not_ready)} 名玩家未准备")
    if not any(p.role == "human" and p.character_id for p in parts):
        # 真人 KP 新模型：KP 本人就是真人，玩家席可全 AI——只要求至少 1 个已入座角色，
        # 防止「零角色开局」的死局；其余模式维持至少 1 名真人玩家。
        if session and session.kp_mode == "human" and session.identity_version >= 2:
            if not any(p.role != "kp" and p.character_id for p in parts):
                gaps.append("至少需要 1 个已入座的角色（真人认领或 AI 队友）")
        else:
            gaps.append("至少需要 1 名真人玩家")
    return gaps


def _lobby_host_guard(db: Session, session_id: str, token: str | None) -> GameSession:
    """大厅内改动座位的共同前置：房间在、还没开局、动手的是房主。

    授权与 /start 同口径（manager 级）：纯本机会话没有「房主」可言，本机即可管理。
    """
    session = db.get(GameSession, session_id)
    if not session:
        raise ValueError("房间不存在")
    if session.status != "setup":
        raise ValueError("游戏已开始，无法调整座位")
    if not (is_host(db, session_id, token) or can_manage_session(db, session_id, token)):
        raise ValueError("只有房主可以调整座位")
    return session


#: 一个房间最多几个玩家席。模组推荐人数最多也就 6~8，留点余量；上不封顶会让大厅被拖垮。
MAX_PLAYER_SEATS = 8


def add_seat(
    db: Session, session_id: str, role: str, token: str | None,
) -> GameSession:
    """在大厅里加一个座位（``role`` 为 ``human`` 空席或 ``ai`` 待指派席）。

    座位是房间的内容，人数在房间里调——建房那一屏只定模组与 KP 模式。
    """
    session = _lobby_host_guard(db, session_id, token)
    if role not in ("human", "ai"):
        raise ValueError("席位类型只能是 human 或 ai")
    parts = get_participants(db, session_id)
    players = [p for p in parts if p.role != "kp"]
    if len(players) >= MAX_PLAYER_SEATS:
        raise ValueError(f"最多 {MAX_PLAYER_SEATS} 个玩家席位")
    session.participants.append(
        SessionParticipant(
            character_id=None,
            role=role,
            is_primary=False,
            seat_order=max((p.seat_order for p in parts), default=-1) + 1,
            claimed=False,
            owner_token=None,
            # AI 席没有「谁来点准备」可言，加进来就是就绪的；真人空席等人认领后自己点。
            ready=role == "ai",
            identity_version=session.identity_version,
        )
    )
    db.commit()
    db.refresh(session)
    return session


def remove_seat(
    db: Session, session_id: str, seat_order: int, token: str | None,
) -> GameSession:
    """在大厅里删掉一个座位。KP 席与房主自己的席位不可删。"""
    session = _lobby_host_guard(db, session_id, token)
    seat = next(
        (p for p in get_participants(db, session_id) if p.seat_order == seat_order), None,
    )
    if seat is None:
        raise ValueError("席位不存在")
    if seat.role == "kp":
        raise ValueError("KP 席位不可删除")
    if seat.owner_token and seat.owner_token == session.host_token:
        raise ValueError("不能删除房主自己的席位")
    players = [p for p in get_participants(db, session_id) if p.role != "kp"]
    if len(players) <= 1:
        raise ValueError("至少要保留一个玩家席位")
    db.delete(seat)
    db.commit()
    db.refresh(session)
    # 主角席被删 → 把锚点让给剩下的第一个座位，否则 player_character_id 会指向已删的角色
    _reseat_primary(db, session)
    return session


def _reseat_primary(db: Session, session: GameSession) -> None:
    """确保还有一个主角席，并让 player_character_id 与它一致（纯修复，无副作用语义）。"""
    players = [p for p in get_participants(db, session.id) if p.role != "kp"]
    if not players:
        return
    if not any(p.is_primary for p in players):
        anchor = next((p for p in players if p.character_id), players[0])
        anchor.is_primary = True
    primary = next((p for p in players if p.is_primary), None)
    session.player_character_id = primary.character_id if primary else None
    db.commit()


def assign_seat_character(
    db: Session, session_id: str, seat_order: int, character_id: str | None,
    token: str | None,
) -> GameSession:
    """给 AI 席指派/清空角色。真人席的入座走 claim_seat（那条路要校验 token 归属）。"""
    session = _lobby_host_guard(db, session_id, token)
    seat = next(
        (p for p in get_participants(db, session_id) if p.seat_order == seat_order), None,
    )
    if seat is None:
        raise ValueError("席位不存在")
    if seat.role != "ai":
        raise ValueError("真人席位请由本人认领")
    if character_id:
        if not db.get(Character, character_id):
            raise ValueError("角色不存在")
        if character_id in active_character_ids(db, exclude_session_id=session_id):
            raise ValueError("该角色正在其他房间或游戏中")
    seat.character_id = character_id
    seat.ready = True          # AI 席没有「谁来点准备」可言
    db.commit()
    db.refresh(session)
    _reseat_primary(db, session)
    return session


def kick_seat(
    db: Session, session_id: str, seat_order: int, token: str | None
) -> tuple[GameSession, str]:
    """房主把某真人席位的玩家移出，席位回到空席待认领。返回 (session, 被踢角色名)。"""
    session = db.get(GameSession, session_id)
    if not session:
        raise ValueError("房间不存在")
    if session.status != "setup":
        raise ValueError("游戏已开始，无法移出席位")
    if not is_host(db, session_id, token):
        raise ValueError("只有房主可以移出玩家")
    seat = (
        db.query(SessionParticipant)
        .filter(
            SessionParticipant.session_id == session_id,
            SessionParticipant.seat_order == seat_order,
        )
        .first()
    )
    if not seat:
        raise ValueError("席位不存在")
    # 旧房间主角席就是房主，不能踢；新模型房主可能在 KP 席，主角席是普通玩家，允许被踢。
    if seat.is_primary and (
        session.host_token is None or seat.owner_token == session.host_token
    ):
        raise ValueError("不能移出房主自己")
    if seat.role != "human":
        raise ValueError("只能移出真人玩家")
    char = db.get(Character, seat.character_id) if seat.character_id else None
    name = char.name if char else "玩家"
    seat.character_id = None
    seat.owner_token = None
    seat.claimed = False
    seat.ready = False
    db.commit()
    db.refresh(session)
    if seat.is_primary:
        # 与 claim_seat 同源：主角席被清空/换人后，快捷字段必须同步，
        # 否则被移出的角色会继续被 active_character_ids 当作「占用中」。
        _reseat_primary(db, session)
    return session, name


def start_game(db: Session, session_id: str, token: str | None) -> GameSession:
    """房主校验 + 门槛校验后把房间从 setup 推进到 active。"""
    session = db.get(GameSession, session_id)
    if not session:
        raise ValueError("房间不存在")
    if session.status != "setup":
        raise ValueError("房间不在大厅状态")
    # 纯本机会话（建局时没带 token，主角席也无归属）没有「房主」可言——is_host 对
    # token=None 恒为 False，若只认它，这类局会永远卡在大厅开不了。从前它们建完直接
    # active、根本不走这里，所以这个缺口是「建局恒进大厅」之后才暴露出来的。
    # 判定与 can_manage_session 的「无归属 → 本机可管理」保持同一口径。
    if not (is_host(db, session_id, token) or can_manage_session(db, session_id, token)):
        raise ValueError("只有房主可以开始游戏")
    gaps = lobby_gaps(db, session_id)
    if gaps:
        raise ValueError("；".join(gaps))
    purge_lobby_chat(db, session_id)
    session.status = "active"
    db.commit()
    db.refresh(session)
    return session


def purge_lobby_chat(db: Session, session_id: str) -> int:
    """清掉开局前的大厅聊天，返回删除条数。

    大厅聊天是**开局前的场外协商**（「你带医生我带记者」「等我五分钟」），开局那一刻
    就失去全部价值：它既不进 KP 上下文、也不参与向量召回（见 event_recall 的
    `INDEXABLE_TYPES` 明确排除 ooc），却会一直躺在存档里、混进「卷宗」检索
    （`SEARCHABLE_EVENT_TYPES` 含 ooc）。

    只在 setup→active 这一次调用，所以删掉的必然是大厅那批；开局后游戏内的 OOC
    照常入库、照常保留。
    """
    return (
        db.query(EventLog)
        .filter(EventLog.session_id == session_id, EventLog.event_type == "ooc")
        .delete(synchronize_session=False)
    )


def resolve_actor(
    db: Session, session_id: str, token: str | None, acting_character_id: str | None,
) -> Character:
    """自由式多人：校验并返回本次行动的角色（按 token 校验席位归属）。"""
    session = db.get(GameSession, session_id)
    if not session:
        raise ValueError("房间不存在")
    target_id = acting_character_id or session.player_character_id
    if not target_id:
        raise ValueError("未指定行动角色")
    parts = get_participants(db, session_id)
    seat = next((p for p in parts if p.character_id == target_id), None)
    if not seat:
        raise ValueError("该角色不在本房间")
    if seat.role != "human":
        raise ValueError("只能以真人席位行动")
    # 席位有归属时必须校验 token：缺 token 或不匹配一律拒绝（此前『token 为空即放行』
    # 会让攻击者不带 X-Player-Token 头就冒充任意有主席位）。无归属席位（纯本机旧会话）放行。
    if seat.owner_token and seat.owner_token != (token or ""):
        raise ValueError("无权以该角色行动")
    char = db.get(Character, target_id)
    if not char:
        raise ValueError("角色不存在")
    return char


def resolve_ooc_actor(
    db: Session,
    session_id: str,
    token: str | None,
    acting_character_id: str | None,
) -> tuple[str | None, str]:
    """解析大厅 OOC 身份；允许已认领但尚未绑定角色的真人席位发言。"""
    if acting_character_id:
        char = resolve_actor(db, session_id, token, acting_character_id)
        return char.id, char.name

    session = db.get(GameSession, session_id)
    if not session:
        raise ValueError("房间不存在")

    parts = get_participants(db, session_id)
    if token:
        seat = next(
            (
                p for p in parts
                if p.owner_token == token and p.role in ("human", "kp")
            ),
            None,
        )
        if seat:
            if seat.character_id:
                char = db.get(Character, seat.character_id)
                if not char:
                    raise ValueError("角色不存在")
                return char.id, char.name
            return None, "真人 KP" if seat.role == "kp" else "玩家"

    # 保留纯本机旧会话的无 token 兼容行为。
    char = resolve_actor(db, session_id, token, None)
    return char.id, char.name


def resolve_token_actor(
    db: Session,
    session_id: str,
    token: str | None,
) -> Character:
    """按 token 解析当前真人角色；纯本机旧会话回落到主角。"""
    session = db.get(GameSession, session_id)
    if not session:
        raise ValueError("房间不存在")

    parts = get_participants(db, session_id)
    if token:
        seat = next(
            (
                p
                for p in parts
                if p.owner_token == token and p.role == "human" and p.character_id
            ),
            None,
        )
        if seat:
            char = db.get(Character, seat.character_id)
            if not char:
                raise ValueError("角色不存在")
            return char

    # 只要会话已有任何席位归属，就不能把缺失或错误 token 回退成主角。
    if any(p.owner_token for p in parts):
        raise ValueError("无权以该角色行动")

    target_id = session.player_character_id
    if not target_id:
        raise ValueError("未指定行动角色")
    seat = next((p for p in parts if p.character_id == target_id), None)
    if seat and seat.role != "human":
        raise ValueError("只能以真人席位行动")
    char = db.get(Character, target_id)
    if not char:
        raise ValueError("角色不存在")
    return char


def get_party_members(
    db: Session, session_id: str, exclude_id: str | None = None,
) -> list[Character]:
    """会话内所有已填角色（真人 + AI），可排除某角色；用于 KP 整队上下文。"""
    out: list[Character] = []
    for p in get_participants(db, session_id):
        if not p.character_id or p.character_id == exclude_id:
            continue
        c = db.get(Character, p.character_id)
        if c:
            out.append(c)
    return out


def is_human_controlled(db: Session, session_id: str, char_id: str | None) -> bool:
    """该角色是否由真人控制（用于决定检定是「待玩家投骰」还是系统自动掷）。

    有 human 席位认领该角色即真人；找不到席位时，主角默认按真人处理（兼容未建席位的旧会话）。
    """
    if not char_id:
        return False
    part = (
        db.query(SessionParticipant)
        .filter(
            SessionParticipant.session_id == session_id,
            SessionParticipant.character_id == char_id,
        )
        .first()
    )
    if part is not None:
        return part.role == "human"
    sess = db.get(GameSession, session_id)
    return bool(sess and sess.player_character_id == char_id)




def get_ai_teammates(db: Session, session_id: str) -> list[Character]:
    """返回会话内所有 AI 队友角色，按席位顺序。"""
    parts = (
        db.query(SessionParticipant)
        .filter(
            SessionParticipant.session_id == session_id,
            SessionParticipant.role == "ai",
        )
        .order_by(SessionParticipant.seat_order.asc())
        .all()
    )
    teammates: list[Character] = []
    for p in parts:
        char = db.get(Character, p.character_id)
        if char:
            teammates.append(char)
    return teammates


def get_session(db: Session, session_id: str) -> GameSession | None:
    return db.get(GameSession, session_id)


def list_sessions(db: Session) -> list[GameSession]:
    return db.query(GameSession).order_by(GameSession.created_at.desc()).all()


def list_sessions_for_token(
    db: Session, token: str | None
) -> list[GameSession]:
    """按 token 过滤为「我参与的会话」，避免客人连上主机后看到房主的全部私有存档。

    可见规则：
      - 会话内没有任何有主席位（纯本机/旧会话，无归属）→ 本机可见，保持原体验；
      - 否则仅当 token 拥有其中某个席位时可见。
    """
    out: list[GameSession] = []
    for s in list_sessions(db):
        owner_tokens = {p.owner_token for p in s.participants if p.owner_token}
        if not owner_tokens:
            out.append(s)
        elif token and token in owner_tokens:
            out.append(s)
    return out


def can_view_session(
    db: Session,
    session_id: str,
    token: str | None,
    *,
    allow_open_lobby: bool = True,
) -> bool:
    """判断请求方是否可以读取会话级资源。

    读取权限与 ``list_sessions_for_token`` 保持同一套口径：
    - 旧存档/纯本机会话没有任何 owner token，保持匿名可读；
    - 有归属的会话必须命中任一席位 owner token；
    - setup 阶段仍有空真人席时，允许访客读取大厅所需资源，认领后自动收紧。
    """
    session = db.get(GameSession, session_id)
    if session is None:
        return False

    participants = get_participants(db, session_id)
    owner_tokens = {p.owner_token for p in participants if p.owner_token}
    if not owner_tokens:
        return True
    if token and token in owner_tokens:
        return True
    if allow_open_lobby and session.status == "setup":
        return any(p.role == "human" and not p.claimed for p in participants)
    return False


def update_session_status(db: Session, session_id: str, status: str) -> GameSession | None:
    session = db.get(GameSession, session_id)
    if not session:
        return None
    session.status = status
    db.commit()
    db.refresh(session)
    return session


def update_session_style(
    db: Session,
    session_id: str,
    narrative_style: str | None,
    image_style: str | None,
) -> GameSession | None:
    """改本局的文风 / 画风。None=不动该项，空串=改回「继承模组默认」。

    不做预设 id 校验：非预设值本来就是合法的自定义原文（取值约定见 style_presets），
    在这里挡一道只会把自定义功能挡掉。
    """
    session = db.get(GameSession, session_id)
    if not session:
        return None
    if narrative_style is not None:
        session.narrative_style = narrative_style.strip()
    if image_style is not None:
        session.image_style = image_style.strip()
    db.commit()
    db.refresh(session)
    return session








def can_manage_session(db: Session, session_id: str, token: str | None) -> bool:
    """房主管理权（结束会话、删除等破坏性/房主操作）：房主本人；或纯本机/旧会话
    （主角席无归属）时的本机用户。有主会话只允许房主，防同网段他人越权。"""
    session = db.get(GameSession, session_id)
    if session and session.host_token is not None:
        return bool(token and token == session.host_token)
    seat = _primary_seat(db, session_id)
    if seat is None:
        return False
    if not seat.owner_token:  # 纯本机/旧会话，无归属 → 本机可管理（保持原体验）
        return True
    return bool(token and seat.owner_token == token)


def can_delete_session(db: Session, session_id: str, token: str | None) -> bool:
    """删除会话的鉴权，语义同 can_manage_session（房主或纯本机会话）。"""
    return can_manage_session(db, session_id, token)


def delete_session(db: Session, session_id: str) -> bool:
    session = db.get(GameSession, session_id)
    if not session:
        return False
    db.query(EventLog).filter(EventLog.session_id == session_id).delete()
    db.delete(session)
    db.commit()
    return True


def set_flag(db: Session, session_id: str, flag: str, value: bool = True) -> None:
    """置/清剧情标志（world_state.flags）。KP 通过 [SET_FLAG]/[CLEAR_FLAG] 推进剧情状态，
    场景/NPC 的状态变体据此切换。flag 名做轻量规范化（去空白），value=False 即清除该标志。"""
    flag = (flag or "").strip()
    if not flag:
        return
    session = db.get(GameSession, session_id)
    if not session:
        return
    ws = dict(session.world_state or {})
    flags = dict(ws.get("flags") or {})
    if value:
        flags[flag] = True
    else:
        flags.pop(flag, None)
    ws["flags"] = flags
    session.world_state = ws
    db.commit()
