from __future__ import annotations

import re
import secrets

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.models.character import Character
from app.models.event_log import EventLog
from app.models.module import Module
from app.models.session import GameSession
from app.models.session_participant import SessionParticipant
from app.services import world_memory

# 「仅 KP 可见」的 visibility 哨兵：带此哨兵的事件（如幕后推演）只进 KP 上下文，
# 对一切玩家侧出口（历史/重连分页、搜索、AI 队友上下文、NPC 上下文、广播）全部不可见。
KP_ONLY_SENTINEL = "kp"


def is_kp_only_event(ev: EventLog) -> bool:
    """该事件是否「仅 KP 可见」（visibility 含 kp 哨兵）——玩家侧查询一律过滤。"""
    return KP_ONLY_SENTINEL in (ev.visibility or [])


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
    ids = {s.player_character_id for s in sessions if s.player_character_id}
    session_ids = [s.id for s in sessions]
    if session_ids:
        parts = (
            db.query(SessionParticipant)
            .filter(SessionParticipant.session_id.in_(session_ids))
            .all()
        )
        ids |= {p.character_id for p in parts}
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
        world_state={"visited_scenes": [first_scene_id] if first_scene_id else []},
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


def add_pending_check(db: Session, session_id: str, check: dict) -> None:
    """登记一个「待玩家投骰」的检定（turn_state.pending_checks，按 check_id 存）。"""
    session = db.get(GameSession, session_id)
    if not session:
        return
    ws = dict(session.turn_state or {})
    pending = dict(ws.get("pending_checks") or {})
    pending[check["id"]] = check
    ws["pending_checks"] = pending
    session.turn_state = ws
    db.commit()


def get_pending_check(
    db: Session,
    session_id: str,
    check_id: str,
) -> dict | None:
    """按 id 读取待投检定，不移除状态。"""
    session = db.get(GameSession, session_id)
    if not session:
        return None
    pending = (session.turn_state or {}).get("pending_checks") or {}
    check = pending.get(check_id)
    return dict(check) if isinstance(check, dict) else None


def find_pending_check(
    db: Session, session_id: str, char_id: str | None, skill: str, difficulty: str,
) -> dict | None:
    """查是否已存在等价的待投检定（同 角色+技能+难度）。用于去重——分头行动下同一 plan 注入
    每个分组，多组会各自吐出同一条 [DICE_CHECK]，合并处理会重复挂 pending / 弹重复投骰卡。"""
    session = db.get(GameSession, session_id)
    if not session:
        return None
    pending = (session.turn_state or {}).get("pending_checks") or {}
    for c in pending.values():
        if (
            c.get("char_id") == char_id
            and c.get("skill") == skill
            and (c.get("difficulty") or "normal") == (difficulty or "normal")
        ):
            return c
    return None


def find_pending_san_check(
    db: Session, session_id: str, char_id: str, source: str,
) -> dict | None:
    """查找同一角色、同一恐怖源尚未完成的 SAN 检定。"""
    session = db.get(GameSession, session_id)
    if not session:
        return None
    pending = (session.turn_state or {}).get("pending_checks") or {}
    for check in pending.values():
        if (
            isinstance(check, dict)
            and check.get("kind") == "san_check"
            and check.get("char_id") == char_id
            and (check.get("source") or "") == (source or "")
        ):
            return dict(check)
    return None


def append_pending_batch_result(
    db: Session, session_id: str, batch_id: str, description: str,
) -> int:
    """把已完成结果追加到同批剩余待投项，返回仍待投的人数。"""
    session = db.get(GameSession, session_id)
    if not session:
        return 0
    ws = dict(session.turn_state or {})
    pending = dict(ws.get("pending_checks") or {})
    remaining = 0
    for check_id, raw in list(pending.items()):
        if not isinstance(raw, dict) or raw.get("san_batch_id") != batch_id:
            continue
        check = dict(raw)
        results = list(check.get("san_results") or [])
        results.append(description)
        check["san_results"] = results
        pending[check_id] = check
        remaining += 1
    if remaining:
        ws["pending_checks"] = pending
        session.turn_state = ws
        db.add(session)
        db.commit()
    return remaining


def append_pending_group_check_result(
    db: Session,
    session_id: str,
    batch_id: str,
    description: str,
    *,
    succeeded: bool,
    fumbled: bool,
) -> int:
    """把一名真人的公开群检结果追加到同批剩余待投项，并合并批次结果标志。"""
    session = db.get(GameSession, session_id)
    if not session:
        return 0
    ws = dict(session.turn_state or {})
    pending = dict(ws.get("pending_checks") or {})
    remaining = 0
    for check_id, raw in list(pending.items()):
        if not isinstance(raw, dict) or raw.get("check_batch_id") != batch_id:
            continue
        check = dict(raw)
        results = list(check.get("check_results") or [])
        results.append(description)
        check["check_results"] = results
        check["check_any_success"] = bool(check.get("check_any_success")) or succeeded
        check["check_any_fumble"] = bool(check.get("check_any_fumble")) or fumbled
        pending[check_id] = check
        remaining += 1
    if remaining:
        ws["pending_checks"] = pending
        session.turn_state = ws
        db.add(session)
        db.commit()
    return remaining


def pop_pending_check(db: Session, session_id: str, check_id: str) -> dict | None:
    """取出并移除一个待定检定；不存在返回 None。"""
    session = db.get(GameSession, session_id)
    if not session:
        return None
    ws = dict(session.turn_state or {})
    pending = dict(ws.get("pending_checks") or {})
    check = pending.pop(check_id, None)
    if check is None:
        return None
    ws["pending_checks"] = pending
    session.turn_state = ws
    db.commit()
    return check


def rollback_last_kp_output(db: Session, session_id: str) -> int:
    """回滚「最新一次 KP 会话」的叙事产物，供玩家「重新生成」用。

    删除范围 = 最后一条『玩家方（真人玩家 + AI 队友）行动/发言』之后的：
      - KP 旁白（narration）
      - NPC 台词（dialogue 且行动者不属于玩家方）
      - 待玩家投骰的检定请求（system + metadata.check_request），并清掉对应 pending_checks
    刻意**保留**：玩家/队友的行动与发言、已投出的骰子结果（dice，不重掷）、HP/场景等其他 system。

    这样「重新生成」= 拿本轮玩家与队友的既有输入、以及已定的骰子，重新生成 KP 叙事，
    而不会重跑队友回合、也不会重掷已定的检定。返回删除的事件条数。
    """
    session = db.get(GameSession, session_id)
    if not session:
        return 0
    party_ids = {
        p.character_id
        for p in db.query(SessionParticipant)
        .filter(SessionParticipant.session_id == session_id)
        .all()
    }
    if session.player_character_id:
        party_ids.add(session.player_character_id)

    events = get_session_events(db, session_id, limit=0)
    last_input = -1
    for i, ev in enumerate(events):
        if ev.event_type in ("action", "dialogue") and ev.actor_id in party_ids:
            last_input = i

    removed = 0
    removed_check_ids: list[str] = []
    for ev in events[last_input + 1:]:
        meta = ev.metadata_ or {}
        is_narration = ev.event_type == "narration"
        is_npc_dialogue = ev.event_type == "dialogue" and ev.actor_id not in party_ids
        is_check_request = ev.event_type == "system" and meta.get("check_request")
        if not (is_narration or is_npc_dialogue or is_check_request):
            continue
        if is_check_request and meta.get("id"):
            removed_check_ids.append(meta["id"])
        db.delete(ev)
        removed += 1

    if removed_check_ids:
        ts = dict(session.turn_state or {})
        pending = dict(ts.get("pending_checks") or {})
        for cid in removed_check_ids:
            pending.pop(cid, None)
        ts["pending_checks"] = pending
        session.turn_state = ts

    if removed:
        db.commit()
    return removed


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


def get_session_events(
    db: Session, session_id: str, limit: int = 0, offset: int = 0
) -> list[EventLog]:
    """按 sequence_num 升序返回会话事件；默认 limit=0 即全量。

    默认必须是「全量」而非截断：本函数只服务于生成/上下文构建路径，它们要的是完整对话史
    （由 build_kp_context 的 token 预算 + 滚动摘要游标负责裁剪成实际喂给 LLM 的窗口）。
    早先默认 limit=100 会因升序取到「最早的 100 条」——会话过百条后 KP 上下文里全是旧事件、
    看不到最新玩家输入，导致跑团错乱。前端历史/重连分页走的是另一个 get_latest_events
    （带 before_seq），不受此默认影响。
    """
    q = (
        db.query(EventLog)
        .filter(EventLog.session_id == session_id)
        .order_by(EventLog.sequence_num.asc())
        .offset(offset)
    )
    if limit > 0:
        q = q.limit(limit)
    return q.all()


def get_latest_events(
    db: Session, session_id: str, limit: int = 50, before_seq: int | None = None,
) -> tuple[list[EventLog], bool]:
    """前端历史/重连分页用的最新事件页（升序返回）。

    「仅 KP 可见」事件（visibility 含 kp 哨兵，如幕后推演）在此过滤——本端点面向
    所有玩家，幕后事件永远不下发前端。过滤在取页之后做（幕后事件稀疏），某页可能
    略少于 limit，但 has_more/before_seq 分页语义不受影响。
    """
    q = db.query(EventLog).filter(EventLog.session_id == session_id)
    if before_seq is not None:
        q = q.filter(EventLog.sequence_num < before_seq)
    q = q.order_by(EventLog.sequence_num.desc())
    rows = q.limit(limit + 1).all()
    has_more = len(rows) > limit
    results = [e for e in rows[:limit] if not is_kp_only_event(e)]
    results.reverse()
    return results, has_more


#: 参与检索的事件类型（系统提示、幕后推演等噪音不进）。
SEARCHABLE_EVENT_TYPES = ("narration", "dialogue", "action", "dice", "ooc")

#: 检索结果片段的长度上限，以及关键词左侧留多少上文。
SNIPPET_CHARS = 160
SNIPPET_LEAD = 40


def search_snippet(content: str, query: str, width: int = SNIPPET_CHARS) -> str:
    """截一段**以命中处为中心**的片段，两端截断处补省略号。

    原先是无脑取正文前 140 字。一段旁白动辄两三百字，关键词落在后半截时切下来的
    片段里根本看不到它——玩家看到的就是「这条明明不含关键词，怎么也被搜出来了」。
    匹配是在全文上做的，展示也得对得上。
    """
    text = (content or "").strip()
    q = (query or "").strip()
    if len(text) <= width:
        return text
    idx = text.lower().find(q.lower()) if q else -1
    if idx < 0:                                  # 查不到（大小写/空白差异）→ 退回取开头
        return text[:width].rstrip() + "…"
    start = max(0, idx - SNIPPET_LEAD)
    end = min(len(text), start + width)
    start = max(0, min(start, end - width))      # 命中在结尾附近时把窗口往左推满
    return ("…" if start > 0 else "") + text[start:end].strip() + ("…" if end < len(text) else "")


def search_events(
    db: Session, session_id: str, query: str, limit: int = 20,
    offset: int = 0, order: str = "desc",
) -> tuple[list[EventLog], int]:
    """在本局历史里模糊检索（content LIKE），返回 (本页事件, 命中总数)。

    ``order``：``desc`` 由新到旧（默认，最近发生的先看），``asc`` 由旧到新。
    命中总数供前端画分页——没有它，用户不知道自己在多大的结果集里翻。
    """
    q = (query or "").strip()
    if not q:
        return [], 0
    like = f"%{q}%"
    base = db.query(EventLog).filter(
        EventLog.session_id == session_id,
        EventLog.content.like(like),
        EventLog.event_type.in_(SEARCHABLE_EVENT_TYPES),
    )
    total = base.count()
    col = EventLog.sequence_num
    rows = (
        base.order_by(col.asc() if order == "asc" else col.desc())
        .offset(max(0, offset))
        .limit(max(1, limit))
        .all()
    )
    # 双保险：幕后事件（event_type=system）本就被类型过滤挡住，这里再按 kp 哨兵
    # 显式过滤一次，防未来搜索范围扩大后泄露「仅 KP 可见」内容。
    return [e for e in rows if not is_kp_only_event(e)], total


def human_character_ids(db: Session, session_id: str) -> set[str]:
    """本会话所有真人席位的角色 id（回合确认制里需要逐个确认推进的主体）。"""
    return {
        p.character_id
        for p in get_participants(db, session_id)
        if p.role == "human" and p.character_id
    }


def set_turn_confirm(db: Session, session_id: str, char_id: str, confirmed: bool) -> None:
    """记录/撤销某真人角色对『本回合推进』的确认（存 turn_state 列——回合锁拆出 world_state）。"""
    session = db.get(GameSession, session_id)
    if not session or not char_id:
        return
    tc = dict(session.turn_state or {})
    if confirmed:
        tc[char_id] = True
    else:
        tc.pop(char_id, None)
    session.turn_state = tc
    db.commit()


def turn_confirm_state(
    db: Session, session_id: str, online_tokens: set[str] | None = None
) -> dict:
    """当前回合确认进度：{confirmed_ids, total, ready}。ready＝所有「需确认」真人都已确认。

    掉线豁免：给定 online_tokens 时，有归属但不在线的真人自动豁免——否则任一玩家关掉
    浏览器就会让整局永久卡死。无归属席位（纯本机会话）一律计入（无法判在线，按在场处理）。
    不给 online_tokens（旧调用/测试）时退化为「所有真人都需确认」的原行为。
    """
    session = db.get(GameSession, session_id)
    humans = [
        p for p in get_participants(db, session_id)
        if p.role == "human" and p.character_id
    ]
    if online_tokens is not None:
        humans = [
            p for p in humans
            if (not p.owner_token) or (p.owner_token in online_tokens)
        ]
    required_ids = {p.character_id for p in humans}
    tc = (session.turn_state or {}) if session else None
    tc = tc or {}
    confirmed = sorted(cid for cid in required_ids if tc.get(cid))
    total = len(required_ids)
    return {
        "confirmed_ids": confirmed,
        "total": total,
        "ready": total > 0 and len(confirmed) >= total,
    }


def commit_turn(db: Session, session_id: str) -> None:
    """推进：把本回合所有『暂存发言』(metadata.pending_turn) 转正（去标记），并清空确认状态。"""
    session = db.get(GameSession, session_id)
    if not session:
        return
    for ev in get_session_events(db, session_id, limit=0):
        meta = ev.metadata_ or {}
        if meta.get("pending_turn"):
            m = dict(meta)
            m.pop("pending_turn", None)
            ev.metadata_ = m
            flag_modified(ev, "metadata_")
    # 只清回合确认，不清 turn_state 里的其它键（pending_checks / pending_item_gains /
    # item_delta_keys 有各自的消费/作废时机，不能随推进一起抹掉）。
    ts = dict(session.turn_state or {})
    ts["turn_confirm"] = {}
    session.turn_state = ts
    db.commit()


def delete_pending_event(db: Session, session_id: str, event_id: str, actor_id: str) -> bool:
    """删除一条『本回合暂存』发言：仅限本人、仅限 pending_turn（未推进）。返回是否删除。"""
    ev = db.get(EventLog, event_id)
    if not ev or ev.session_id != session_id:
        return False
    if not ev.actor_id or ev.actor_id != actor_id:
        return False
    if not (ev.metadata_ or {}).get("pending_turn"):
        return False
    db.delete(ev)
    db.commit()
    return True


def update_pending_event(
    db: Session, session_id: str, event_id: str, actor_id: str, content: str,
) -> bool:
    """改写一条『本回合暂存』发言的正文：仅限本人、仅限 pending_turn（未推进）。返回是否改写。"""
    ev = db.get(EventLog, event_id)
    if not ev or ev.session_id != session_id:
        return False
    if not ev.actor_id or ev.actor_id != actor_id:
        return False
    if not (ev.metadata_ or {}).get("pending_turn"):
        return False
    ev.content = content
    db.add(ev)
    db.commit()
    return True


def get_next_sequence_num(db: Session, session_id: str) -> int:
    result = (
        db.query(EventLog.sequence_num)
        .filter(EventLog.session_id == session_id)
        .order_by(EventLog.sequence_num.desc())
        .first()
    )
    return (result[0] + 1) if result else 1


def add_event(
    db: Session,
    session_id: str,
    event_type: str,
    content: str,
    actor_id: str | None = None,
    actor_name: str = "",
    visibility: list[str] | None = None,
    metadata: dict | None = None,
    group: str | None = None,
) -> EventLog:
    meta = dict(metadata or {})
    # 分头行动：同一回合里不同分组/场景的内容，用 group 标签分栏渲染（KP 经 [GROUP] 标注）。
    if group:
        meta["group"] = group
    # 给事件打上「发生在哪个场景」的戳：NPC 上下文据此只看自己所在场景的事件，
    # 避免一个 NPC 知道玩家在别处发生的事（信息隔离）。调用方未显式给 scene_id 时取当前场景。
    if "scene_id" not in meta:
        sess = db.get(GameSession, session_id)
        if sess and sess.current_scene_id:
            meta["scene_id"] = sess.current_scene_id

    # sequence_num 由「读最大值 + 1」生成，多个请求并发时可能同时读到同一个值。
    # 唯一约束负责兜底，遇到撞号只回滚本次 INSERT 并重新取最大值；其它完整性错误原样抛出。
    for attempt in range(3):
        event = EventLog(
            session_id=session_id,
            sequence_num=get_next_sequence_num(db, session_id),
            event_type=event_type,
            actor_id=actor_id,
            actor_name=actor_name,
            content=content,
            visibility=visibility or [],
            metadata_=meta,
        )
        db.add(event)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            message = str(exc).lower()
            is_sequence_conflict = (
                "uq_event_logs_session_sequence" in message
                or "event_logs.session_id, event_logs.sequence_num" in message
            )
            if not is_sequence_conflict or attempt == 2:
                raise
            continue
        db.refresh(event)
        return event

    # 理论上第三次尝试会在 attempt == 2 时直接抛出；保留显式异常避免静态分析认为无返回。
    raise RuntimeError("事件序号分配失败")


def set_event_group(db: Session, event: EventLog, group: str) -> None:
    """给已落库的事件补打分组标签（分头行动：把本回合各角色行动归入其所在场景列）。"""
    meta = dict(event.metadata_ or {})
    if meta.get("group") == group:
        return
    meta["group"] = group
    event.metadata_ = meta
    flag_modified(event, "metadata_")  # JSON 列原地改字典不会被脏检测，需显式标记
    db.add(event)
    db.commit()


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


def update_scene(db: Session, session_id: str, scene_id: str) -> None:
    session = db.get(GameSession, session_id)
    if not session:
        return
    session.current_scene_id = scene_id
    ws = dict(session.world_state or {})
    visited = ws.get("visited_scenes", [])
    if scene_id not in visited:
        visited.append(scene_id)
    ws["visited_scenes"] = visited
    session.world_state = ws
    db.commit()


# ── 按角色位置 / 已知地点（分头行动 + 大地图前往）──────────────────

def get_party_locations(session: GameSession) -> dict:
    """world_state.party_locations：{角色 id: 所在场景 id}。缺省时按需回落到当前场景。"""
    return dict((session.world_state or {}).get("party_locations") or {})


def get_char_location(session: GameSession, char_id: str | None) -> str | None:
    """某角色当前所在场景；无显式记录则回落到会话当前场景（向后兼容）。"""
    if not char_id:
        return session.current_scene_id
    return get_party_locations(session).get(char_id) or session.current_scene_id


def set_char_location(db: Session, session_id: str, char_id: str, scene_id: str) -> None:
    """把某角色移动到某场景（玩家经大地图前往 / AI 队友分头时的落点）。

    主角移动时一并更新 current_scene_id（地图面板、NPC 上下文等仍以它为锚）。目的地记入已访问。
    """
    if not (char_id and scene_id):
        return
    session = db.get(GameSession, session_id)
    if not session:
        return
    ws = dict(session.world_state or {})
    locs = dict(ws.get("party_locations") or {})
    locs[char_id] = scene_id
    ws["party_locations"] = locs
    visited = list(ws.get("visited_scenes") or [])
    if scene_id not in visited:
        visited.append(scene_id)
    ws["visited_scenes"] = visited
    session.world_state = ws
    if char_id == session.player_character_id:
        session.current_scene_id = scene_id
    db.commit()


#: 「本轮明确留守」的事件标记。位置默认跟随队伍，只有显式表过态的人自理。
STAY_META_KEY = "stay"


def stayed_char_ids(db: Session, session_id: str) -> set[str]:
    """本回合明确说过「我留在这儿」的角色 id。

    队伍位置的默认语义是**跟随**：没有位置记录的人被 `get_char_location` 回落到当前场景，
    主角一走就跟着走。这对绝大多数回合是对的，但它让「你们仨去街区，我留下继续问诺特」
    这个意图在系统里无处安放——莫妮卡人被判定在街区，KP 却还在演她留在事务所。

    留守做成**本轮事件**而不是持久状态：它描述的是这一次移动跟不跟，不是一种长期属性；
    队友下一轮想跟上，正常行动即可，不必再撤销一个标志位。
    """
    from app.services.turn_context import _current_turn_events

    out: set[str] = set()
    for ev in _current_turn_events(get_session_events(db, session_id)):
        meta = ev.metadata_ or {}
        if meta.get(STAY_META_KEY) and ev.actor_id:
            out.add(str(ev.actor_id))
    return out


# 地点名常见的「设施类型」后缀：按长度从长到短，供从场景标题析出可被对话提及的关键词。
_FACILITY_SUFFIXES = [
    "疗养院", "图书馆", "档案馆", "博物馆", "派出所", "警察局", "礼拜堂", "老房子",
    "报社", "医院", "教堂", "法院", "老宅", "宅邸", "公寓", "旅馆", "酒店",
    "饭店", "学校", "大学", "中学", "小学", "墓地", "墓园", "工厂", "仓库", "教会",
    "庄园", "别墅", "城堡", "监狱", "银行", "邮局", "车站", "码头", "农场", "矿场",
    "洞穴", "地窖", "街区", "房子", "宅", "街",
]


# 「状态修饰」后缀：跟在设施类型之后表示地点当下状态（沉思礼拜堂+废墟）。它们**只用于剥离**
# 得到核心地名，本身**不**作为解锁关键词（否则说个「废墟」就乱解锁）。
_MODIFIER_SUFFIXES = ["废墟", "遗址", "旧址", "遗迹", "残址", "废址", "旧宅"]


def derive_scene_keywords(title: str) -> set[str]:
    """从场景标题**确定性地**派生解锁关键词：完整标题 + 核心地名（剥离废墟/遗址等状态词）+
    设施类型后缀 + 专名前缀。玩家在对话/行动里提到其中任意一个即解锁该地点。

    例：
      「罗克斯伯里疗养院」→ {完整标题, "疗养院", "罗克斯伯里"}
      「沉思礼拜堂废墟」  → {完整标题, "沉思礼拜堂", "礼拜堂", "沉思"}
        （此前只得完整标题，故必须说全名才解锁——本函数补上核心名与专名）

    这是**兜底/派生**逻辑：新模组解析时会另外生成并存储更丰富的 keywords（含地址/俗称），
    运行时二者取并集（见 known_scene_ids）。
    """
    title = (title or "").strip()
    if not title:
        return set()
    keywords = {title}
    # 先剥掉结尾的状态修饰后缀（可叠多个）得到核心地名，核心名本身入库
    core = title
    changed = True
    while changed:
        changed = False
        for suf in _MODIFIER_SUFFIXES:
            if core.endswith(suf) and len(core) > len(suf):
                core = core[: -len(suf)].strip("·的 ")
                changed = True
    if core != title and len(core) >= 2:
        keywords.add(core)
    # 对核心名跑设施后缀逻辑：加设施类型别名（礼拜堂）+ 最长后缀前的专名（沉思）
    matched = [suf for suf in _FACILITY_SUFFIXES if core.endswith(suf) and len(core) > len(suf)]
    keywords.update(matched)
    if matched:
        longest = max(matched, key=len)
        prefix = core[: -len(longest)].strip("·的 ")
        if len(prefix) >= 2:
            keywords.add(prefix)
    return {k for k in keywords if len(k) >= 2}


def scene_unlock_keywords(scene: dict) -> set[str]:
    """一个场景的全部解锁关键词 = 存储的 keywords（解析时生成，含地址/俗称）∪ 标题派生关键词。
    存储缺失（老模组）时退化为纯派生——这样『沉思礼拜堂废墟』说「沉思礼拜堂」也能解锁。"""
    stored = {
        k.strip() for k in (scene.get("keywords") or [])
        if isinstance(k, str) and len(k.strip()) >= 2
    }
    title = scene.get("title") or scene.get("name") or ""
    return stored | derive_scene_keywords(title)


_NUMBERED_CARRIAGE_RE = re.compile(r"(?<!\d)(\d+)\s*号车厢")
_NEXT_CARRIAGE_RE = re.compile(r"下一(?:节|个)?车厢")


def _carriage_number(scene: dict | None) -> int | None:
    """从场景名或解锁词中读取阿拉伯数字车厢号。"""
    if not scene:
        return None
    labels = [scene.get("title"), scene.get("name"), *(scene.get("keywords") or [])]
    for label in labels:
        if not isinstance(label, str):
            continue
        matched = _NUMBERED_CARRIAGE_RE.search(label)
        if matched:
            return int(matched.group(1))
    return None


def _relative_carriage_mentions(by_id: dict, event) -> set[str]:
    """解析「下一节车厢」这种依赖叙事所在场景的相对称呼。

    只在事件带 ``metadata.scene_id``、且目标确实与来源场景直连时解析，避免把旧叙事按
    会话当前场景重新解释，也避免顺着编号提前揭露不可达地点。
    """
    content = getattr(event, "content", "") or ""
    if not _NEXT_CARRIAGE_RE.search(content):
        return set()
    metadata = getattr(event, "metadata_", None) or {}
    source_id = metadata.get("scene_id")
    source = by_id.get(source_id)
    source_number = _carriage_number(source)
    if source_number is None:
        return set()
    connected = {str(sid) for sid in (source.get("connections") or []) if sid}
    connected.update(
        sid for sid, scene in by_id.items()
        if source_id in {str(item) for item in (scene.get("connections") or []) if item}
    )
    return {
        sid for sid in connected
        if _carriage_number(by_id.get(sid)) == source_number + 1
    }


def known_scene_ids(module, session: GameSession, events: list | None = None) -> set:
    """已知地点 = 已访问/当前所在 ∪ 玩家可见叙事中被提及过的场景。

    未访问、且玩家可见内容从未提及的地点不在大地图上显示。KP-only 幕后事件绝不能
    参与解锁；带场景元数据的「下一节车厢」等确定性相对称呼也会解析为真实场景。
    """
    by_id = {s.get("id"): s for s in (module.scenes or []) if s.get("id")}
    known = set((session.world_state or {}).get("visited_scenes") or [])
    if session.current_scene_id:
        known.add(session.current_scene_id)
    visible_events = [
        event for event in (events or [])
        if getattr(event, "event_type", None) in ("narration", "dialogue", "action", "system")
        and KP_ONLY_SENTINEL not in (getattr(event, "visibility", None) or [])
    ]
    convo = "\n".join(
        (getattr(event, "content", "") or "") for event in visible_events
    )
    if convo:
        for sid, s in by_id.items():
            if sid in known:
                continue
            if any(kw in convo for kw in scene_unlock_keywords(s)):
                known.add(sid)
    for event in visible_events:
        known.update(_relative_carriage_mentions(by_id, event))
    return {sid for sid in known if sid in by_id}


def _scene_adjacency(module) -> dict[str, set[str]]:
    """场景连通图（``connections`` 的无向闭包）：作者单向填写也按双向通行。"""
    adj: dict[str, set[str]] = {}
    ids = {s.get("id") for s in (module.scenes or []) if s.get("id")}
    for s in (module.scenes or []):
        sid = s.get("id")
        if not sid:
            continue
        adj.setdefault(sid, set())
        for c in s.get("connections") or []:
            c = str(c or "").strip()
            if c and c != sid and c in ids:
                adj.setdefault(c, set())
                adj[sid].add(c)
                adj[c].add(sid)
    return adj


def scene_neighbors(module, scene_id: str | None) -> list[str]:
    """当前场景可直达的相邻场景 id（升序）。无图/无该场景返回空列表。"""
    if not scene_id:
        return []
    return sorted(_scene_adjacency(module).get(scene_id, ()))


def find_scene_path(
    module, start: str | None, dest: str, via_allowed: set[str] | None = None,
) -> list[str] | None:
    """沿场景连通图找 ``start → dest`` 的最短路径（BFS，路径含两端点）。

    返回场景 id 列表；**确实不连通**返回 None（调用方据此拒绝切换）。
    保守把关——以下情形一律视为可直达（返回平凡路径，行为与没有连通图时一致）：
    - 模组任何场景都没填 connections（旧/手工模组，没建图，不能把它们走死）；
    - start 缺失（无当前位置可依据）；
    - start 或 dest 自身没有任何边（作者没为该地点建边，无拓扑可循）。

    ``via_allowed`` 给定时，**途经**的场景必须落在这个集合里（终点不受此限——终点正是要
    去的新地方）。玩家发起的移动一律带上它并传「已到访场景」：光看图上连通会放行「取道
    一个从没去过的车厢抵达先头车厢」这种路线，而抵达叙述又明确「途经不停留、不触发事件」，
    合起来就是**无视沿途的怪物与门锁凭空穿过去**。想去更远的地方，就得一段一段真的走过去。
    """
    dest = (dest or "").strip()
    if not dest:
        return None
    if not start:
        return [dest]
    if start == dest:
        return [start]
    adj = _scene_adjacency(module)
    if not any(adj.values()):
        return [start, dest]
    if not adj.get(start) or not adj.get(dest):
        return [start, dest]
    seen = {start}
    queue: list[list[str]] = [[start]]
    while queue:
        path = queue.pop(0)
        for nxt in sorted(adj.get(path[-1], ())):
            if nxt in seen:
                continue
            if nxt == dest:
                return path + [nxt]
            seen.add(nxt)
            # 只有允许途经的场景才继续往下扩；起点永远算数（人就站在那儿）。
            if via_allowed is not None and nxt not in via_allowed:
                continue
            queue.append(path + [nxt])
    return None


def travel_blocker(
    module, session: GameSession, start: str | None, dest: str,
) -> tuple[str, str] | None:
    """玩家为什么去不了 ``dest``：返回 (挡路的场景 id, 原因)；能去则 None。

    只在 find_scene_path(..., via_allowed=可通行) 判定去不了时才有意义——用图上连通的
    完整路径反查第一个过不去的途经点，好把「不连通」这种没头没脑的报错换成
    「要去先头车厢，得先过 2 号车厢」。真的完全不连通时返回 None（那是另一种拒绝）。
    """
    full = find_scene_path(module, start, dest)
    if not full:
        return None
    visited = visited_scene_ids(session)
    blocked = world_memory.blocked_scenes(session.world_state or {})
    for sid in full[1:-1]:
        if sid in blocked:
            return sid, blocked[sid] or "那边过不去"
        if sid not in visited:
            return sid, "那儿你们还没去过"
    return None


def passable_scene_ids(session: GameSession) -> set[str]:
    """可作为**途经点**的场景：去过的，减去当前被 KP 判定过不去的。

    终点不受此限——走进一个危险或封锁的地方是玩家的自由，被拦住的是「借道穿过去」。
    """
    return visited_scene_ids(session) - set(
        world_memory.blocked_scenes(session.world_state or {})
    )


def visited_scene_ids(session: GameSession) -> set[str]:
    """队伍真正到过的场景（含当前所在）——「能否途经」的唯一判据。

    与 known_scene_ids 的区别是本文件里最容易混的一处：``known`` 含「叙述里被提到过」的
    地点（它们要在大地图上看得见、可作为**终点**），``visited`` 只含真正去过的
    （只有这些能当**途经点**）。玩家听说过驾驶室，不等于知道怎么绕过中间那节车厢。
    """
    out = set((session.world_state or {}).get("visited_scenes") or [])
    if session.current_scene_id:
        out.add(session.current_scene_id)
    for sid in ((session.world_state or {}).get("party_locations") or {}).values():
        if sid:
            out.add(str(sid))
    return out


def list_known_locations(
    module, session: GameSession, char_id: str | None = None, events: list | None = None,
    char_names: dict[str, str] | None = None, reveal_all: bool = False,
) -> list[dict]:
    """供「大地图/调查板/沙盘」渲染：已知地点列表（当前所在、已访问、相互连接、队友分布）。

    - ``kind == "chapter"`` 的场景是叙事章节而非地点，不上图（当前正身处其中时除外）。
    - ``connections`` 只回展示集合内的邻居——玩家侧未知地点绝不经边泄露。
    - ``char_names``（char_id → 名字）给定时，按 party_locations 归并各地点的在场成员。
    - ``reveal_all=True``（真人 KP 上帝视角）：返回全部 location 场景并附 ``known`` 标记，
      前端「玩家视角」开关据此纯客户端过滤；玩家侧永远走迷雾路径（known 恒 True）。
    """
    by_id = {s.get("id"): s for s in (module.scenes or []) if s.get("id")}
    visited = set((session.world_state or {}).get("visited_scenes") or [])
    cur = get_char_location(session, char_id)
    # 层级门禁：挂在某个父级地点之下的场景，父级被**真正到过**之前一律不可见。
    # 这既是防剧透（开局就不该知道村里有几间屋子），也让子沙盘有个明确的解锁时刻。
    # 判据用 visited 而不是 known：听说过村庄不等于进过村、更不等于看得见村里的门牌。
    # 队伍当前所在的场景永不隐藏（分头行动时有人已经在里面了）。
    from app.services import hex_map

    def _unlocked(sid: str) -> bool:
        parent = hex_map.scene_parent(by_id.get(sid))
        return not parent or parent in visited or sid == cur

    known = {
        sid for sid in known_scene_ids(module, session, events)
        if (by_id[sid].get("kind") != "chapter" or sid == cur) and _unlocked(sid)
    }
    if reveal_all:
        # KP 上帝视角看得见全部（含未解锁的子级），由 known 标记如实告诉他玩家看不看得见
        shown = {sid for sid, s in by_id.items() if s.get("kind") != "chapter" or sid == cur}
    else:
        shown = known
    # 队伍分布：各成员所在场景（party_locations 缺省回落主场景）
    party_at: dict[str, list[str]] = {}
    if char_names:
        pl = (session.world_state or {}).get("party_locations") or {}
        for cid, name in char_names.items():
            sid = pl.get(cid) or session.current_scene_id
            if sid:
                party_at.setdefault(sid, []).append(name)
    # 调查板红线：**已发现**的线索（clue_ledger）按其模组定义的 location 挂到地点上。
    # 只含玩家已触碰的线索——未发现的绝不上板（不剧透）。
    ledger = (session.world_state or {}).get("clue_ledger") or {}
    clue_by_id = {c.get("id"): c for c in (getattr(module, "clues", None) or []) if c.get("id")}
    clues_at: dict[str, list[dict]] = {}
    for cid, entry in ledger.items():
        cdef = clue_by_id.get(cid)
        loc = (cdef or {}).get("location")
        if cdef and loc:
            clues_at.setdefault(loc, []).append({
                "id": cid,
                "name": cdef.get("name") or cid,
                "status": (entry or {}).get("status") or "partial",
            })
    out = []
    for sid in shown:
        s = by_id[sid]
        conns = [c for c in (s.get("connections") or []) if c in shown and c != sid]
        out.append({
            "id": sid,
            "name": s.get("title") or s.get("name") or sid,
            "current": sid == cur,
            "visited": sid in visited,
            "connections": conns,
            "party": party_at.get(sid, []),
            "clues": clues_at.get(sid, []),
            "map": s.get("map"),   # 沙盘坐标与地貌（旧模组未回填时为 None）
            "known": sid in known,  # KP 上帝视角下标记玩家是否已知；玩家侧恒 True
            # 场景配图：前端拿它做「场景氛围底」的色调来源。之所以从这里给而不是只靠聊天流里
            # 那条「抵达」插图消息——那条消息可能压根不在已加载的分页里（存量存档翻页只取最近
            # 一段），而配图生成后是回写进 scene.image 的，这份数据一直都在。
            # 本函数的 cur 取的是**查看者自己**的角色位置，分头行动时各人也就各看各的场景。
            "image": s.get("image") or "",
        })
    out.sort(key=lambda x: (not x["current"], not x["visited"], x["id"]))
    return out


def list_visible_map_nodes(module, locations: list[dict], reveal_all: bool = False) -> list[dict]:
    """返回沙盘需要绘制的统一节点；普通节点只作为地貌，不参与旅行。"""
    nodes = list(getattr(module, "map_nodes", None) or [])
    shown_ids = {str(item.get("id")) for item in locations if item.get("id")}
    out = []
    for node in nodes:
        sid = str(node.get("scene_id") or "")
        if sid:
            if sid in shown_ids:
                out.append(node)
            elif not reveal_all:
                # 未发现的场景保留其地貌格，但清掉 scene_id，让前端不要绘制剧情 token。
                hidden = dict(node)
                hidden["scene_id"] = None
                out.append(hidden)
            continue
        if reveal_all:
            out.append(node)
            continue
        # 普通地貌是地图底图的一部分，完整下发，未知场景不会造成视觉上的“挖空”。
        out.append(node)
    return out


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
