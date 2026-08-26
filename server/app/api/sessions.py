import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import (
    player_token,
    require_session_actor,
    require_session_host,
    require_session_manager,
    require_session_ooc_actor,
    require_session_token_actor,
    require_session_viewer,
)
from app.database import get_db
from app.models.character import Character
from app.models.module import Module
from app.schemas.event import EventRead
from app.schemas.session import (
    ClaimSeatRequest,
    EndVoteRequest,
    ReadyRequest,
    SeatAddRequest,
    SeatAssignRequest,
    SessionCreate,
    SessionRead,
    SessionRuleOptionsRead,
    SessionRuleOptionsUpdate,
    SessionStatusUpdate,
    SessionStyleUpdate,
)
from app.services import rule_options_service, session_service
from app.services.event_protocol import make_chunk as _make_chunk
from app.services import rate_limit, room_sync
from app.services.rate_limit import limiter
from app.services.room_events import RoomEvent
from app.services.turn_orchestrator import (
    initialize_human_session,
    run_epilogue_generation,
    run_opening_generation,
)
from app.services.generation_manager import generation_manager
from app.services.room_hub import encode_sse, room_hub, stream_room

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


def _session_payload(
    session, chars_map: dict[str, str], module_title: str | None,
    token: str | None = None,
) -> dict:
    data = SessionRead.model_validate(session).model_dump()
    online = room_hub.online_tokens(session.id)
    # session.participants 与 data["participants"] 均按 seat_order，可对齐计算 is_mine
    for p, sp in zip(data.get("participants", []), session.participants):
        p["character_name"] = chars_map.get(p["character_id"]) if p["character_id"] else None
        p["is_mine"] = bool(token and sp.owner_token and sp.owner_token == token)
        # 新模型房主身份独立于玩家席；旧房间没有 host_token 时回落到主角席。
        p["is_host"] = bool(
            (session.host_token is not None and sp.owner_token == session.host_token)
            or (session.host_token is None and sp.is_primary and sp.owner_token)
        )
        p["is_online"] = bool(sp.owner_token and sp.owner_token in online)
        p["is_kp"] = sp.role == "kp"
    owned_seats = [sp for sp in session.participants if token and sp.owner_token == token]
    owned_kp_seat = next((sp for sp in owned_seats if sp.role == "kp"), None)
    owned_player_seat = next(
        (sp for sp in owned_seats if sp.role == "human" and sp.character_id), None,
    )
    if owned_kp_seat:
        character_name = "KP"
    elif owned_player_seat:
        character_name = chars_map.get(owned_player_seat.character_id)
    elif owned_seats:
        # 已预留但尚未选择角色的真人席不能回落成房主的主角名。
        character_name = None
    else:
        # 无 token 的本机旧调用、尚未完成身份迁移的存档继续回落到主角快捷字段。
        character_name = (
            chars_map.get(session.player_character_id)
            if session.player_character_id
            else None
        )
    return {
        **data,
        "module_title": module_title,
        "character_name": character_name,
    }


def _chars_map(db: Session, sessions) -> dict[str, str]:
    char_ids: set[str] = set()
    for s in sessions:
        if s.player_character_id:
            char_ids.add(s.player_character_id)
        char_ids.update(p.character_id for p in s.participants if p.character_id)
    if not char_ids:
        return {}
    return {
        c.id: c.name
        for c in db.query(Character).filter(Character.id.in_(char_ids)).all()
    }


@router.post("")
def create_session(
    data: SessionCreate,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    if data.participants:
        seats = [p.model_dump() for p in data.participants]
    else:
        seats = [
            {"character_id": data.player_character_id, "role": "human", "is_primary": True}
        ]
    try:
        session = session_service.create_session(
            db, data.module_id, seats, creator_token=token, kp_mode=data.kp_mode,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    module = db.get(Module, session.module_id)
    return _session_payload(
        session, _chars_map(db, [session]), module.title if module else None, token,
    )


@router.get("")
def list_sessions(
    db: Session = Depends(get_db), token: str | None = Depends(player_token),
):
    sessions = session_service.list_sessions_for_token(db, token)
    module_ids = {s.module_id for s in sessions}
    modules_map = (
        {m.id: m.title for m in db.query(Module).filter(Module.id.in_(module_ids)).all()}
        if module_ids else {}
    )
    chars_map = _chars_map(db, sessions)
    return [
        _session_payload(s, chars_map, modules_map.get(s.module_id), token)
        for s in sessions
    ]


@router.get("/by-code/{room_code}")
@limiter.limit(rate_limit.ROOM_CODE_LOOKUP_LIMIT, exempt_when=rate_limit.exempt_local)
def get_session_by_code(
    request: Request,  # noqa: ARG001 — slowapi 要求端点显式声明才能取到请求
    room_code: str,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    session = session_service.get_session_by_code(db, room_code)
    if not session:
        raise HTTPException(404, "房间不存在或房间码有误")
    module = db.get(Module, session.module_id)
    return _session_payload(
        session, _chars_map(db, [session]), module.title if module else None, token,
    )


@router.post("/{session_id}/join")
@limiter.limit(rate_limit.JOIN_LIMIT, exempt_when=rate_limit.exempt_local)
def join_session(
    request: Request,  # noqa: ARG001 — slowapi 要求端点显式声明才能取到请求
    session_id: str,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    """进入大厅即预留真人席；角色可稍后选择或导入。"""
    try:
        session = session_service.join_session(db, session_id, token or "")
    except ValueError as e:
        raise HTTPException(400, str(e))
    module = db.get(Module, session.module_id)
    payload = _session_payload(
        session, _chars_map(db, [session]), module.title if module else None, token,
    )
    room_hub.broadcast(session_id, _make_chunk("lobby"))
    return payload


@router.post("/{session_id}/claim")
def claim_seat(
    session_id: str,
    data: ClaimSeatRequest,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    try:
        session = session_service.claim_seat(
            db, session_id, data.seat_order, data.character_id, token or "",
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    module = db.get(Module, session.module_id)
    payload = _session_payload(
        session, _chars_map(db, [session]), module.title if module else None, token,
    )
    # 广播入座事件给房间内所有人
    seat = next((p for p in payload["participants"] if p["seat_order"] == data.seat_order), None)
    name = seat["character_name"] if seat and seat["character_name"] else (
        "真人 KP" if seat and seat["role"] == "kp" else "新成员"
    )
    room_hub.broadcast(session_id, _make_chunk("seat", f"{name} 已入座", actor_name=name))
    return payload


@router.post("/{session_id}/ready")
def set_ready(
    session_id: str,
    data: ReadyRequest,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    """大厅：把当前玩家席位的准备态置位，并广播 lobby 刷新。"""
    require_session_token_actor(db, session_id, token)
    try:
        session = session_service.set_ready(db, session_id, token, data.ready)
    except ValueError as e:
        raise HTTPException(400, str(e))
    module = db.get(Module, session.module_id)
    payload = _session_payload(
        session, _chars_map(db, [session]), module.title if module else None, token,
    )
    room_hub.broadcast(session_id, _make_chunk("lobby"))
    return payload


@router.post("/{session_id}/start")
async def start_game(
    session_id: str,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    """大厅：房主开局。校验门槛后 setup→active，并触发开场生成。

    用 manager 级而非 host 级授权：后者刻意不放行「无席位归属的纯本机会话」，而建局改成
    恒进大厅之后，单人本机局（建局不带 token）正是这一类——只认 host 的话它永远开不了。
    开局与删除、踢人同属房间管理动作，口径本就该一致。
    """
    require_session_manager(
        db, session_id, token, detail="只有房主可以开始游戏",
    )
    try:
        session = session_service.start_game(db, session_id, token)
    except ValueError as e:
        raise HTTPException(400, str(e))
    # 推进到 active 后触发开场（fire-and-forget，输出经 /live 下发）。真人 KP 模式只初始化
    # 背景/场景卡，不调用 AI 直接生成玩家可见叙事。
    room_hub.broadcast(session_id, _make_chunk("started"))
    if not generation_manager.is_generating(session_id):
        room_hub.broadcast(session_id, _make_chunk("generating"))
        opening = (
            run_opening_generation(session_id)
            if session.kp_mode == "ai"
            else initialize_human_session(session_id)
        )
        generation_manager.start(session_id, opening)
    module = db.get(Module, session.module_id)
    return _session_payload(
        session, _chars_map(db, [session]), module.title if module else None, token,
    )


@router.post("/{session_id}/kick/{seat_order}")
def kick_seat(
    session_id: str,
    seat_order: int,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    """大厅：房主把某真人席位的玩家移出，席位回到空席。"""
    require_session_host(
        db, session_id, token, detail="只有房主可以移出玩家",
    )
    try:
        session, name = session_service.kick_seat(db, session_id, seat_order, token)
    except ValueError as e:
        raise HTTPException(400, str(e))
    room_hub.broadcast(session_id, _make_chunk("seat", f"{name} 已被移出席位", actor_name=name))
    room_hub.broadcast(session_id, _make_chunk("lobby"))
    module = db.get(Module, session.module_id)
    return _session_payload(
        session, _chars_map(db, [session]), module.title if module else None, token,
    )


def _lobby_payload(db: Session, session, token: str | None) -> dict:
    """座位变动后统一广播 lobby 并回当前房间快照。"""
    room_hub.broadcast(session.id, _make_chunk("lobby"))
    module = db.get(Module, session.module_id)
    return _session_payload(
        session, _chars_map(db, [session]), module.title if module else None, token,
    )


@router.post("/{session_id}/seats")
def add_seat(
    session_id: str,
    data: SeatAddRequest,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    """大厅：加一个座位。人数在房间里调——建房那一屏只定模组与 KP 模式。"""
    try:
        session = session_service.add_seat(db, session_id, data.role, token)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _lobby_payload(db, session, token)


@router.delete("/{session_id}/seats/{seat_order}")
def remove_seat(
    session_id: str,
    seat_order: int,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    """大厅：删一个座位（KP 席与房主自己的席位不可删）。"""
    try:
        session = session_service.remove_seat(db, session_id, seat_order, token)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _lobby_payload(db, session, token)


@router.post("/{session_id}/seats/{seat_order}/character")
def assign_seat_character(
    session_id: str,
    seat_order: int,
    data: SeatAssignRequest,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    """大厅：给 AI 席指派/清空角色（真人席的入座走 /claim）。"""
    try:
        session = session_service.assign_seat_character(
            db, session_id, seat_order, data.character_id, token,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _lobby_payload(db, session, token)


@router.post("/{session_id}/typing")
def typing(
    session_id: str,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    """大厅/游戏：广播'正在输入'（短暂、ephemeral，不入库）。"""
    _actor_id, actor_name = require_session_ooc_actor(db, session_id, token, None)
    room_hub.broadcast(
        session_id,
        _make_chunk("typing", actor_name=actor_name),
    )
    return {"ok": True}


@router.get("/style-options")
def style_options():
    """文风 / 画风的预设清单（供前端下拉）。自定义不在此列——直接填原文即可。

    **必须注册在 `/{session_id}` 之前**：FastAPI 按注册顺序匹配，排在后面的话
    `GET /api/sessions/style-options` 会先命中 `/{session_id}`，被当成一个会话 id 去查。
    """
    from app.services import style_presets

    return style_presets.style_options()


@router.get("/{session_id}")
def get_session(
    session_id: str,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    session = require_session_viewer(db, session_id, token)
    module = db.get(Module, session.module_id)
    return _session_payload(
        session, _chars_map(db, [session]), module.title if module else None, token,
    )


@router.get("/{session_id}/context-estimate")
def get_context_estimate(
    session_id: str,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    """预估下一回合 KP 上下文的 token 占用与模型窗口占比，供判断能否继续跑团。"""
    from app.services.context_estimate import estimate_session_context

    require_session_viewer(db, session_id, token)
    result = estimate_session_context(db, session_id)
    if result is None:
        raise HTTPException(404, "会话不存在或缺少模组/角色")
    return result


@router.get("/{session_id}/rag-stats")
def get_rag_stats(
    session_id: str,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    """本局 RAG（规则书/模组原文检索）用量与命中质量统计——评估检索对跑团的实际帮助。"""
    from app.services import rag_stats, session_stats

    require_session_viewer(db, session_id, token)
    stats = session_stats.get(db, session_id)
    return rag_stats.summarize(stats.rag_stats if stats else None)


@router.get("/{session_id}/recaps")
def list_recaps(
    session_id: str,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    """列出本局已生成的战报（session_recaps 表）。"""
    from app.services import recap_service

    require_session_viewer(db, session_id, token)
    return {"recaps": recap_service.list_recaps(db, session_id)}


@router.post("/{session_id}/recap")
async def generate_recap(
    session_id: str,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    """生成一份章节战报并存入 session_recaps；生成失败返回 502（不落库）。"""
    from app.services import recap_service

    require_session_token_actor(db, session_id, token)
    entry = await recap_service.generate_and_store_recap(db, session_id)
    if entry is None:
        raise HTTPException(502, "战报生成失败（可能无事件或模型未配置），请稍后重试")
    return entry


@router.get("/{session_id}/growth")
def growth_eligible(
    session_id: str,
    character_id: str,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    """列出某角色本局可成长的技能（成功用过的技能）。"""
    from app.services import growth_service

    session = require_session_viewer(db, session_id, token)
    party_ids = {
        p.character_id
        for p in session_service.get_participants(db, session_id)
        if p.character_id
    }
    if session.player_character_id:
        party_ids.add(session.player_character_id)
    if character_id not in party_ids:
        raise HTTPException(403, "角色不属于该会话")
    return {"skills": growth_service.eligible_skills(db, session_id, character_id)}


@router.post("/{session_id}/growth/settle")
def growth_settle(
    session_id: str,
    body: dict,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    """对某角色的可成长技能逐项做成长检定并落库，返回逐项结果。"""
    from app.services import growth_service

    character_id = (body or {}).get("character_id")
    if not character_id:
        raise HTTPException(400, "缺少 character_id")
    require_session_actor(db, session_id, token, character_id)
    result = growth_service.settle_growth(db, session_id, character_id)
    if result is None:
        raise HTTPException(404, "会话或角色不存在")
    return result


@router.get("/{session_id}/improvised-npcs")
def list_improvised_npcs(
    session_id: str,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    """列出本局临场 NPC（KP 临时添加的开口龙套）及其是否已转正，供房主决定收编。"""
    from app.services import promote_service

    require_session_viewer(db, session_id, token)
    return {"improvised_npcs": promote_service.list_improvised(db, session_id)}


@router.post("/{session_id}/improvised-npcs/promote")
async def promote_improvised_npc(
    session_id: str,
    body: dict,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    """把某临场 NPC 受控转正为正式配角（据其既有言行生成 NPC 卡，存会话级）。

    **在场的任一真人玩家**都可操作：发现「这个龙套有戏」的往往是跟他对过戏的那个玩家，
    不一定是房主；多人桌上卡在房主那里只是多一道无谓的摩擦。
    转正本身是增量且可控的——只生成一张会话级 NPC 卡，不改模组本体、不影响其他人的存档。

    生成失败返回 502；未登记的名字返回 404。
    """
    from app.services import promote_service

    require_session_token_actor(db, session_id, token)
    name = (body or {}).get("name")
    if not name:
        raise HTTPException(400, "缺少 name")
    card = await promote_service.promote(db, session_id, name)
    if card is None:
        raise HTTPException(502, "转正失败（可能该名字未登记，或模型未配置），请稍后重试")
    return card


@router.get("/{session_id}/replay")
async def export_replay(
    session_id: str,
    style: str = "novel",
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    """把整局改写成小说体/剧本体 markdown 团记（离线批处理，逐窗改写）。"""
    from app.services import replay_service

    require_session_viewer(db, session_id, token)
    result = await replay_service.export_replay(db, session_id, style)
    if result is None:
        raise HTTPException(404, "会话无可导出的事件")
    return result


# 「ended」不在此列：结束模组必须走全体真人共识投票（/end-vote），房主不能单方置 ended。
_ALLOWED_STATUSES = {"setup", "active", "paused"}


@router.put("/{session_id}/status", response_model=SessionRead)
def update_status(
    session_id: str,
    data: SessionStatusUpdate,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    if data.status not in _ALLOWED_STATUSES:
        raise HTTPException(400, f"非法状态：{data.status}")
    require_session_manager(
        db, session_id, token, detail="只有房主可以变更会话状态",
    )
    session = session_service.update_session_status(db, session_id, data.status)
    if not session:
        raise HTTPException(404, "会话不存在")
    return session


@router.put("/{session_id}/style", response_model=SessionRead)
def update_style(
    session_id: str,
    data: SessionStyleUpdate,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    """改本局的文风 / 画风。空串=改回继承模组默认；None=本次不动该项。

    按房主授权：风格是整桌共享的观感，不该让任一玩家单方面改掉别人的体验。
    """
    require_session_manager(
        db, session_id, token, detail="只有房主可以变更本局的文风与画风",
    )
    session = session_service.update_session_style(
        db, session_id, data.narrative_style, data.image_style,
    )
    if not session:
        raise HTTPException(404, "会话不存在")
    return session


@router.get("/{session_id}/rule-options", response_model=SessionRuleOptionsRead)
def read_rule_options(
    session_id: str,
    db: Session = Depends(get_db),
):
    """读本局家规：显式改过的差异项 + 合并模组默认后的完整生效值。

    不限房主：家规改的是所有人的成败概率，玩家有权知道自己这局在什么规则下掷骰。
    """
    session = session_service.get_session(db, session_id)
    if not session:
        raise HTTPException(404, "会话不存在")
    return SessionRuleOptionsRead(
        options=dict(session.rule_options or {}),
        effective=rule_options_service.resolved_view(db, session),
    )


@router.put("/{session_id}/rule-options", response_model=SessionRuleOptionsRead)
def update_rule_options(
    session_id: str,
    data: SessionRuleOptionsUpdate,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    """改本局家规（房主专属）。整份替换：提交什么就是什么，``{}`` = 全改回默认。

    按房主授权，理由同文风：家规直接改变每个人的成败概率，不该让任一玩家单方面改掉。
    """
    require_session_manager(
        db, session_id, token, detail="只有房主可以变更本局的家规",
    )
    session = session_service.update_session_rule_options(
        db, session_id, data.rule_options,
    )
    if not session:
        raise HTTPException(404, "会话不存在")
    room_hub.broadcast(
        session_id,
        _make_chunk("rule_options", metadata={
            "effective": rule_options_service.resolved_view(db, session),
        }),
    )
    return SessionRuleOptionsRead(
        options=dict(session.rule_options or {}),
        effective=rule_options_service.resolved_view(db, session),
    )


@router.post("/{session_id}/end-vote")
async def vote_end_module(
    session_id: str,
    data: EndVoteRequest | None = None,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    """发起 / 同意「结束模组」。任一真人玩家可投；全体真人一致同意才真正结束（单人时一票即结束）。
    返回 {ended, vote}——vote 为不含 token 的公开投票态。"""
    session = session_service.get_session(db, session_id)
    if not session:
        raise HTTPException(404, "会话不存在")
    if session.status == "ended":
        raise HTTPException(400, "本模组已结束")
    acting = data.acting_character_id if data else None
    actor = require_session_actor(db, session_id, token, acting)
    try:
        ended, vote = session_service.cast_end_vote_for_actor(
            db, session_id, actor.id,
        )
    except ValueError as e:
        raise HTTPException(403, str(e)) from e
    if ended:
        # 达成共识：广播状态变更，各端刷新会话（成长结算 / 最终战报入口据 status==ended 出现）。
        room_hub.broadcast(
            session_id,
            _make_chunk("status", "（全体玩家一致同意，本模组已结束，可进行成长结算与最终战报。）",
                        actor_name="系统"),
        )
        # 收场：尾声叙事 + 真相揭晓 + 幕间休息。只翻个状态就散场太虎头蛇尾——玩家刚演完终局，
        # 该有人把故事讲完、把他们没查到的真相说透。真人 KP 的局不代劳（那是 KP 自己的话）。
        #
        # 排不上就算了：模组**已经结束**是既成事实，绝不能因为配额用尽或恰好有生成在跑
        # 就把这次投票响应变成 429/500。收场是锦上添花，不是结束的前置条件。
        if session.kp_mode == "ai" and not generation_manager.is_generating(session_id):
            try:
                generation_manager.start(session_id, run_epilogue_generation(session_id))
            except Exception:
                logger.exception("收场生成未能启动: session=%s", session_id)
    else:
        # 投票进行中：广播公开投票态，各端更新「已同意 N/M」提示。
        room_hub.broadcast(session_id, _make_chunk("end_vote", metadata={"end_vote": vote}))
    return {"ended": ended, "vote": vote}


@router.delete("/{session_id}/end-vote")
def cancel_end_module_vote(
    session_id: str,
    data: EndVoteRequest | None = None,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    """撤销进行中的结束投票（任一在场真人玩家可撤）。返回公开投票态。"""
    acting = data.acting_character_id if data else None
    actor = require_session_actor(db, session_id, token, acting)
    try:
        vote = session_service.cancel_end_vote_for_actor(
            db, session_id, actor.id,
        )
    except ValueError as e:
        raise HTTPException(403, str(e)) from e
    room_hub.broadcast(session_id, _make_chunk("end_vote", metadata={"end_vote": vote}))
    return {"vote": vote}


@router.get("/{session_id}/events")
def get_events(
    session_id: str,
    limit: int = 50,
    before_seq: int | None = None,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    require_session_viewer(db, session_id, token)
    events, has_more = session_service.get_latest_events(
        db, session_id, limit=limit, before_seq=before_seq,
    )
    return {
        "events": [EventRead.model_validate(e).model_dump() for e in events],
        "has_more": has_more,
    }


@router.delete("/{session_id}")
def delete_session(
    session_id: str,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    require_session_manager(
        db, session_id, token, detail="只有房主可以删除该会话",
    )
    if not session_service.delete_session(db, session_id):
        raise HTTPException(404, "会话不存在")
    return {"ok": True}


@router.post("/{session_id}/opening")
async def trigger_opening(
    session_id: str,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    """fire-and-forget 触发开场生成；输出经 /live 下发。幂等由生成逻辑保证。"""
    require_session_token_actor(db, session_id, token)
    game_session = session_service.get_session(db, session_id)
    if not game_session:
        raise HTTPException(404, "会话不存在")
    if generation_manager.is_generating(session_id):
        return {"ok": True, "already_generating": True}

    room_hub.broadcast(session_id, _make_chunk("generating"))
    opening = (
        run_opening_generation(session_id)
        if game_session.kp_mode == "ai"
        else initialize_human_session(session_id)
    )
    generation_manager.start(session_id, opening)
    return {"ok": True}


@router.get("/{session_id}/generating")
async def check_generating(
    session_id: str,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    require_session_viewer(db, session_id, token)
    return {"generating": generation_manager.is_generating(session_id)}


@router.get("/{session_id}/sync")
def get_sync(
    session_id: str,
    systems: str | None = None,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    """一次取齐所有 ``sync`` 类状态的快照 + 事件水位线，供断线重连对齐。

    此前重连只重拉聊天历史，战斗/追逐态仅在**进页**时各拉一次、回合确认态更是
    连查询端点都没有——断线期间错过那几条广播，HUD 就一直是错的，直到下一次广播。

    ``systems`` 可用逗号分隔限定范围（如 ``combat,turn``），缺省全取。
    """
    session = require_session_viewer(db, session_id, token)
    wanted = [s.strip() for s in systems.split(",")] if systems else None
    return {
        "seq": room_sync.current_seq(db, session_id),
        "generating": generation_manager.is_generating(session_id),
        "systems": room_sync.snapshot(db, session, wanted),
    }


@router.get("/{session_id}/live/_schema", response_model=RoomEvent)
def live_schema() -> RoomEvent:
    """只为契约存在的端点：把 ``RoomEvent`` 带进 OpenAPI。

    SSE 的负载不会出现在 OpenAPI 里（它不是响应体的一部分），这曾经让实时层成为
    前后端契约治理的裸奔区——后端加一种事件类型，前端不会有任何提示。挂上这个
    ``response_model`` 之后，既有的 ``pnpm api:generate`` 就会把类型集合生成到
    ``apps/web/src/api/generated.ts``，前端那份 ``Record<RoomEventType, …>`` 漏一个
    类型就编译不过。

    不要真去调它——返回值只是一个占位样例。
    """
    return RoomEvent(type="ready")


@router.get("/{session_id}/live")
async def live(
    session_id: str,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    """房间级常驻 SSE（仅实时增量）：所有成员订阅，跨多次生成存活。

    历史与重连对齐沿用 ``GET /events``（保留 seq 分页）；本端点只负责实时广播：
    玩家行动、KP 叙事 token、检定、OOC、入座/在场等。客户端先开本连接、再拉历史，
    按事件 id 去重，避免开连接与拉历史之间的竞态丢事件。
    """
    require_session_viewer(db, session_id, token)
    # SSE 会话可能持续数小时，不要让依赖注入的数据库连接贯穿整个流生命周期。
    db.close()

    # subscribe 会把当前生成的 in-flight buffer 立即重放给中途接入者
    q = room_hub.subscribe(session_id, token)
    generating = generation_manager.is_generating(session_id)
    # 通知房间内其他人：有人上线（触发各端刷新在线态）
    room_hub.broadcast(session_id, _make_chunk("presence"))

    async def gen():
        # ready 携带订阅后捕获的权威生成态：客户端据此同步 streaming，不再依赖独立的
        # GET /generating（它与订阅之间有竞态：若生成恰在两者之间结束，done 会被漏收，
        # 导致客户端 streaming 卡在 true、界面永远显示「整理笔记」且输入锁死，需刷新才恢复）。
        # 这两条不经 broadcast（只给本连接），所以要自己走传输层的编码。
        yield encode_sse(_make_chunk("ready", metadata={"generating": generating}))
        if generating:
            yield encode_sse(_make_chunk("generating"))
        async for chunk in stream_room(session_id, q, token):
            yield chunk

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
