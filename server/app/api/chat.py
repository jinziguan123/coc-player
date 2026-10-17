from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import (
    player_token,
    require_session_actor,
    require_session_kp,
    require_session_manager,
    require_session_ooc_actor,
    require_session_token_actor,
    require_session_viewer,
)
from app.database import get_db
from app.models.module import Module
from app.models.session import GameSession
from app.schemas.event import (
    AdvanceRequest,
    ChatRequest,
    CheckRequest,
    EventEditRequest,
    LuckDecisionRequest,
    RollRequest,
    TravelRequest,
)
from app.schemas.session import KpActionRequest
from app.services import (
    human_kp_desk, human_kp_service, module_service, rate_limit, session_service,
)
from app.services.event_protocol import (
    MAX_OOC_CHARS,
    event_to_chunk,
    make_chunk as _make_chunk,
    ooc_text,
    split_ooc,
    split_speech_action,
)
from app.services.human_kp_actions import execute_human_kp_action
from app.services.turn_context import commit_pending_travel
from app.services.turn_orchestrator import (
    run_chat_generation,
    run_check_request_generation,
    run_luck_decision,
    run_regenerate_generation,
    run_roll_generation,
    run_travel_generation,
)
from app.services.generation_manager import generation_manager
from app.services.rate_limit import limiter
from app.services.room_hub import room_hub

router = APIRouter(prefix="/api/sessions", tags=["chat"])


def _continue_human_kp_turn(
    db: Session, session_id: str, game_session: GameSession,
) -> None:
    """按 KP 私有偏好运行 AI 队友，或直接通知真人 KP 接管。"""
    if (
        human_kp_service.auto_ai_teammates_enabled(game_session)
        and session_service.get_ai_teammates(db, session_id)
        and human_kp_service.has_unprocessed_player_turn(db, session_id, game_session)
    ):
        room_hub.broadcast(session_id, _make_chunk("generating"))
        generation_manager.start(
            session_id, human_kp_service.run_human_team_turn_generation(session_id),
        )
        return
    room_hub.broadcast(
        session_id,
        _make_chunk("kp_turn_ready", "玩家回合已提交，等待真人 KP 处理"),
    )


def _commit_ready_turn(
    db: Session, session_id: str, game_session: GameSession,
) -> None:
    """所有在线真人已确认：暂存发言转正；真人 KP 模式只通知 KP，不启动 AI 生成。"""
    session_service.commit_turn(db, session_id)
    if game_session.kp_mode == "human":
        # 真人 KP 不会进入 AI 生成管线，因此必须在此处完成前往动作的位置同步。
        commit_pending_travel(db, session_id)
        _continue_human_kp_turn(db, session_id, game_session)
    else:
        room_hub.broadcast(session_id, _make_chunk("generating"))
        generation_manager.start(session_id, run_chat_generation(session_id))


def _reevaluate_turn_on_leave(session_id: str) -> None:
    """有人断开 /live：在线集合变小，剩下的人可能都已确认推进。

    不重判的话，已确认者的「推进」按钮已禁用，只能等房主/KP 强制推进——掉线豁免形同虚设。
    无人确认时 ready 为假（要求 total>0 且全员确认），不会凭空推进。
    """
    from app import database

    if generation_manager.is_generating(session_id):
        return
    db = database.SessionLocal()
    try:
        game_session = db.get(GameSession, session_id)
        if not game_session or game_session.status != "active":
            return
        # 没推进也要广播：分母随在线集合变了，「已确认 N/M」得跟着刷新。
        state = session_service.live_turn_state(db, session_id)
        room_hub.broadcast(session_id, _make_chunk("turn_state", metadata=state))
        if state["ready"]:
            _commit_ready_turn(db, session_id, game_session)
    finally:
        db.close()


room_hub.add_leave_listener(_reevaluate_turn_on_leave)


@router.post("/{session_id}/ooc")
@limiter.limit(rate_limit.OOC_LIMIT, exempt_when=rate_limit.exempt_local)
def post_ooc(
    request: Request,  # noqa: ARG001 — slowapi 要求端点显式声明才能取到请求
    session_id: str,
    data: ChatRequest,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    """纯 OOC（场外）消息：入库并向全房间广播，不进入 KP 上下文、不触发任何生成。"""
    game_session = db.get(GameSession, session_id)
    if not game_session:
        raise HTTPException(404, "会话不存在")
    actor_id, actor_name = require_session_ooc_actor(
        db, session_id, token, data.acting_character_id,
    )

    text = ooc_text(data.content)
    if not text:
        raise HTTPException(400, "消息不能为空")
    if len(text) > MAX_OOC_CHARS:
        raise HTTPException(400, f"消息太长了（上限 {MAX_OOC_CHARS} 字）")
    ev = session_service.add_event(
        db, session_id, "ooc", text,
        actor_id=actor_id, actor_name=actor_name,
    )
    room_hub.broadcast(session_id, event_to_chunk(ev))
    return {"ok": True, "id": ev.id}


@router.post("/{session_id}/chat")
async def chat(
    session_id: str,
    data: ChatRequest,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    """fire-and-forget：校验 + 落库玩家行动并广播 + 触发生成；输出统一经 /live 下发。"""
    game_session = db.get(GameSession, session_id)
    if not game_session:
        raise HTTPException(404, "会话不存在")
    if game_session.status != "active":
        raise HTTPException(400, "会话未处于活跃状态")
    if generation_manager.is_generating(session_id):
        raise HTTPException(409, "KP 正在叙事，请稍候")

    player_char = require_session_actor(
        db, session_id, token, data.acting_character_id,
    )

    in_character, ooc = split_ooc(data.content)
    if not in_character:
        # 只拒绝、不落库：从前这里先存了一条 OOC 再报错，发送方看到失败、别人却已收到
        raise HTTPException(400, "该消息为纯场外发言，请使用 OOC 通道")

    # 正式行动：按引号约定把言（dialogue）与行（action）分流，按原文顺序逐条落库。
    # 引号内=说出口的台词，引号外=行动；不含引号则整条按行动。
    # 玩家事件的广播随生成一起进 in-flight buffer（见 generation_manager.start 的 prelude），
    # 这样断线重连能重放，避免「点了发送但自己的消息没显示、只剩思考中」的吞消息问题。
    # 回合确认制：玩家发言只进入「本回合暂存」（打 pending_turn 标记、实时广播给同桌），
    # 不立即触发 KP。要等所有真人各自点「推进」确认后（见 /advance），才整批交 KP。
    segments = split_speech_action(in_character) or [("action", in_character)]
    for kind, seg_text in segments:
        ev = session_service.add_event(
            db, session_id, kind, seg_text,
            actor_id=player_char.id, actor_name=player_char.name,
            metadata={"pending_turn": True},
        )
        room_hub.broadcast(session_id, event_to_chunk(ev))
    if ooc:
        ev_ooc = session_service.add_event(
            db, session_id, "ooc", ooc,
            actor_id=player_char.id, actor_name=player_char.name,
        )
        room_hub.broadcast(session_id, event_to_chunk(ev_ooc))

    # 有新发言即撤销本人已有的「确认」（改动后需重新确认），并把最新确认进度广播给同桌。
    session_service.set_turn_confirm(db, session_id, player_char.id, False)
    room_hub.broadcast(
        session_id,
        _make_chunk("turn_state", metadata=session_service.live_turn_state(db, session_id, token)),
    )
    return {"ok": True}


@router.post("/{session_id}/kp/action")
async def kp_action(
    session_id: str,
    data: KpActionRequest,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    """真人 KP M1 工具桌：执行确定性动作并把结果广播给房间。"""
    session = require_session_kp(db, session_id, token)
    if generation_manager.is_generating(session_id):
        raise HTTPException(409, "当前仍有动作处理中，请稍候")
    module = db.get(Module, session.module_id)
    if module is None:
        raise HTTPException(404, "模组不存在")
    try:
        chunks, result = await execute_human_kp_action(
            db, session_id, session, module, data.action, data.payload,
        )
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    for chunk in chunks:
        room_hub.broadcast(session_id, chunk)
    room_hub.broadcast(
        session_id,
        # 具体结果只通过当前 KP 的鉴权 HTTP 响应返回。广播只作为动作完成信号，
        # 防止暗投结果经所有成员共享的 SSE 通道泄露。
        _make_chunk("kp_action", metadata={"action": data.action}),
    )
    # 结果另记进 KP 私有台账（暗投点数下一次动作后还能回看），同样只经 KP 鉴权接口读出
    db.refresh(session)
    human_kp_desk.record_ledger(db, session, data.action, result, chunks)
    if data.action == "narration" and not session_service.human_character_ids(db, session_id):
        # 全 AI 玩家席没人点「推进」：KP 每发一段旁白就是一轮，自动队友在这里接上
        _maybe_auto_team_turn(db, session_id, session)
    return {"ok": True, "result": result}


def _maybe_auto_team_turn(db: Session, session_id: str, game_session: GameSession) -> None:
    if (
        human_kp_service.auto_ai_teammates_enabled(game_session)
        and session_service.get_ai_teammates(db, session_id)
        and human_kp_service.has_unprocessed_player_turn(db, session_id, game_session)
        and not generation_manager.is_generating(session_id)
    ):
        room_hub.broadcast(session_id, _make_chunk("generating"))
        generation_manager.start(
            session_id, human_kp_service.run_human_team_turn_generation(session_id),
        )


@router.post("/{session_id}/check")
async def check(
    session_id: str,
    data: CheckRequest,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    """玩家『申请』技能检定（不指定难度）：交 KP 裁定是否需要、用什么难度。

    KP 判定需要时会挂出「待玩家投骰」的提示，玩家再调 /roll 投骰。"""
    game_session = db.get(GameSession, session_id)
    if not game_session:
        raise HTTPException(404, "会话不存在")
    if game_session.status != "active":
        raise HTTPException(400, "会话未处于活跃状态")
    if generation_manager.is_generating(session_id):
        raise HTTPException(409, "KP 正在叙事，请稍候")
    if not data.skill.strip():
        raise HTTPException(400, "未指定检定技能")

    actor = require_session_actor(
        db, session_id, token, data.acting_character_id,
    )

    skill = data.skill.strip()
    intent = data.intent.strip()
    # 落一条可见行动记录：申请检定这件事本身要留痕（供其他玩家看到、KP 后续上下文也能看到），
    # 带上 intent 是因为光报技能名时，现场若同时有多条线索/多个可疑点，KP 猜不出具体目标。
    content = f"（申请「{skill}」检定：{intent}）" if intent else f"（申请「{skill}」检定）"

    if data.stash:
        # 暂存模式（与大地图「前往」同一模式）：申请进本回合暂存，和这一轮的台词、行动
        # 一起交给 KP。skill/intent 一并落进 metadata——推进时据此**确定性**取出，
        # 不靠 planner 从「（申请「侦查」检定）」这行文本里再认一遍（暂存的前往也是这么做的）。
        ev = session_service.add_event(
            db, session_id, "action", content,
            actor_id=actor.id, actor_name=actor.name,
            metadata={
                "pending_turn": True, "check_request": True,
                "skill": skill, "intent": intent,
            },
        )
        room_hub.broadcast(session_id, event_to_chunk(ev))
        session_service.set_turn_confirm(db, session_id, actor.id, False)
        room_hub.broadcast(
            session_id,
            _make_chunk("turn_state", metadata=session_service.live_turn_state(db, session_id, token)),
        )
        return {"ok": True, "stashed": True}

    human_kp = game_session.kp_mode == "human"
    ev = session_service.add_event(
        db, session_id, "action", content,
        actor_id=actor.id, actor_name=actor.name,
        # 真人 KP 局：带上技能与意图，进 KP 待办（一键照此发起检定），不靠 toast 记住
        metadata={"check_request": True, "skill": skill, "intent": intent} if human_kp else None,
    )
    room_hub.broadcast(session_id, event_to_chunk(ev))
    if human_kp:
        room_hub.broadcast(
            session_id,
            _make_chunk(
                "kp_request", "玩家申请了一次检定，等待真人 KP 裁定",
                metadata={"skill": skill, "intent": intent, "actor_id": actor.id},
            ),
        )
        return {"ok": True, "waiting_for_kp": True}
    room_hub.broadcast(session_id, _make_chunk("generating"))
    generation_manager.start(
        session_id,
        run_check_request_generation(session_id, actor.id, skill, intent),
    )
    return {"ok": True}


@router.post("/{session_id}/roll")
async def roll(
    session_id: str,
    data: RollRequest,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    """玩家点『投骰』：对一个待定检定掷骰，结果交 KP 据达成等级续写（fire-and-forget）。"""
    actor = require_session_token_actor(db, session_id, token)
    game_session = db.get(GameSession, session_id)
    if game_session.status != "active":
        raise HTTPException(400, "会话未处于活跃状态")
    if generation_manager.is_generating(session_id):
        raise HTTPException(409, "KP 正在叙事，请稍候")
    if not data.check_id.strip():
        raise HTTPException(400, "未指定检定")
    pending = session_service.get_pending_check(
        db, session_id, data.check_id.strip(),
    )
    if pending and pending.get("char_id") not in (None, actor.id):
        raise HTTPException(403, "无权提交其他角色的检定")

    room_hub.broadcast(session_id, _make_chunk("generating"))
    generation_manager.start(
        session_id,
        run_roll_generation(session_id, data.check_id.strip()),
    )
    return {"ok": True}


@router.post("/{session_id}/luck-decision")
async def decide_luck(
    session_id: str,
    data: LuckDecisionRequest,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    """对「要不要花幸运扭转这一骰」拍板，随后系统把结算链走完（fire-and-forget）。

    只有这一骰的主人能拍板：花的是他自己的幸运值，成败也记在他头上。
    """
    actor = require_session_token_actor(db, session_id, token)
    game_session = db.get(GameSession, session_id)
    if game_session.status != "active":
        raise HTTPException(400, "会话未处于活跃状态")
    pending = session_service.get_pending_luck(db, session_id)
    if not pending:
        raise HTTPException(404, "没有待决的幸运消费")
    if pending.get("char_id") not in (None, actor.id):
        raise HTTPException(403, "只有这次检定的角色本人能决定是否消费幸运")
    # 重复点击 / 两端同时拍板：第一次已经把结算链跑起来了，第二次给 409 而不是 500
    if generation_manager.is_generating(session_id):
        raise HTTPException(409, "这一骰正在结算，请稍候")

    room_hub.broadcast(session_id, _make_chunk("generating"))
    generation_manager.start(
        session_id,
        run_luck_decision(session_id, bool(data.spend), bool(data.reroll)),
    )
    return {"ok": True}


@router.post("/{session_id}/regenerate")
async def regenerate(
    session_id: str,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    """重新生成最新一轮 KP 叙事：打断卡住的生成 → 回滚上一轮 KP 叙事产物 → 用玩家与队友的既有
    输入（保留已定骰子，不重掷）重跑 KP。

    高风险操作（可能明显改变剧情走向），前端须二次确认后才调用；只作用于「最新一轮」——回滚逻辑
    天然只清理事件流尾部的 KP 产物。

    只有房主能用：它会打断并回滚整桌共享的叙事，不该由任一玩家单方决定。房主不在线时
    放开给在座真人——否则房主掉线后生成一旦卡住，整桌就没人解得开。
    """
    require_session_token_actor(db, session_id, token)
    if not session_service.can_manage_session(db, session_id, token):
        host = session_service.host_token_of(db, session_id)
        if host and host in room_hub.online_tokens(session_id):
            raise HTTPException(403, "只有房主可以重新生成（房主不在线时在座玩家可用）")
    game_session = db.get(GameSession, session_id)
    if game_session.status != "active":
        raise HTTPException(400, "会话未处于活跃状态")
    if game_session.kp_mode == "human":
        raise HTTPException(400, "真人 KP 模式不支持 AI 叙事重生成，请由 KP 重新发布内容")

    # ①打断卡住/进行中的旧生成（其半截叙事会先落库，②随后被回滚清掉）
    await generation_manager.cancel(session_id)
    removed = session_service.rollback_last_kp_output(db, session_id)
    # 回滚可能复原了角色卡（撤销本轮 KP 判的扣血），让各端刷新数值
    room_hub.broadcast(session_id, _make_chunk("character_update"))

    room_hub.broadcast(session_id, _make_chunk("generating"))
    generation_manager.start(session_id, run_regenerate_generation(session_id))
    return {"ok": True, "removed": removed}


@router.post("/{session_id}/advance")
async def advance(
    session_id: str,
    data: AdvanceRequest,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    """玩家点『推进本回合』：记录该真人的确认；所有真人都确认后，把本回合暂存发言整批交 KP
    （先跑 AI 队友回合，再 KP 叙事）。"""
    game_session = db.get(GameSession, session_id)
    if not game_session:
        raise HTTPException(404, "会话不存在")
    if game_session.status != "active":
        raise HTTPException(400, "会话未处于活跃状态")
    if generation_manager.is_generating(session_id):
        raise HTTPException(409, "KP 正在叙事，请稍候")
    actor = require_session_actor(
        db, session_id, token, data.acting_character_id,
    )

    session_service.set_turn_confirm(db, session_id, actor.id, True)
    # 掉线豁免：按在线 token 计算需确认者（并入本次确认者，防其 /live 恰好瞬断被漏算）。
    state = session_service.live_turn_state(db, session_id, token)
    room_hub.broadcast(session_id, _make_chunk("turn_state", metadata=state))

    if state["ready"]:
        _commit_ready_turn(db, session_id, game_session)
    return {"ok": True, "ready": state["ready"]}


@router.post("/{session_id}/force-advance")
async def force_advance(
    session_id: str,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    """房主强制推进：跳过未确认者（掉线/挂机），直接把本回合暂存发言整批交 KP。

    掉线豁免虽已让在线玩家能推进，但极端情形（有人挂机不点、或掉线判定滞后）仍可能卡住；
    此端点给房主一个确定性的兜底出口。仅房主、真人 KP 或纯本机会话可用。
    """
    game_session = db.get(GameSession, session_id)
    if not game_session:
        raise HTTPException(404, "会话不存在")
    if game_session.status != "active":
        raise HTTPException(400, "会话未处于活跃状态")
    # 真人 KP 推进本就是他的职责：旧模型里 KP 席可能不是房主，也要能兜底
    if not session_service.is_kp(db, session_id, token):
        require_session_manager(
            db, session_id, token, detail="只有房主或 KP 可以强制推进",
        )
    if generation_manager.is_generating(session_id):
        raise HTTPException(409, "KP 正在叙事，请稍候")
    session_service.commit_turn(db, session_id)
    if game_session.kp_mode == "human":
        commit_pending_travel(db, session_id)
    room_hub.broadcast(session_id, _make_chunk("turn_state", metadata={"confirmed_ids": [], "total": 0, "ready": True}))
    if game_session.kp_mode == "human":
        _continue_human_kp_turn(db, session_id, game_session)
    else:
        room_hub.broadcast(session_id, _make_chunk("generating"))
        generation_manager.start(session_id, run_chat_generation(session_id))
    return {"ok": True, "ready": True}


@router.patch("/{session_id}/events/{event_id}")
async def edit_event(
    session_id: str,
    event_id: str,
    data: EventEditRequest,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    """改写自己『本回合暂存』的发言（仅未推进、仅本人）；改后需重新确认推进。"""
    game_session = db.get(GameSession, session_id)
    if not game_session:
        raise HTTPException(404, "会话不存在")
    if generation_manager.is_generating(session_id):
        raise HTTPException(409, "KP 正在叙事，请稍候")
    content = data.content.strip()
    if not content:
        raise HTTPException(400, "内容不能为空")
    actor = require_session_actor(
        db, session_id, token, data.acting_character_id,
    )

    if not session_service.update_pending_event(db, session_id, event_id, actor.id, content):
        raise HTTPException(403, "只能修改自己本回合尚未推进的发言")
    session_service.set_turn_confirm(db, session_id, actor.id, False)
    room_hub.broadcast(session_id, _make_chunk("event_update", content, event_id=event_id))
    room_hub.broadcast(
        session_id,
        _make_chunk("turn_state", metadata=session_service.live_turn_state(db, session_id, token)),
    )
    return {"ok": True}


@router.delete("/{session_id}/events/{event_id}")
async def delete_event(
    session_id: str,
    event_id: str,
    acting_character_id: str | None = None,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    """删除自己『本回合暂存』的发言（仅未推进、仅本人）；删后需重新确认推进。"""
    game_session = db.get(GameSession, session_id)
    if not game_session:
        raise HTTPException(404, "会话不存在")
    if generation_manager.is_generating(session_id):
        raise HTTPException(409, "KP 正在叙事，请稍候")
    actor = require_session_actor(
        db, session_id, token, acting_character_id,
    )

    if not session_service.delete_pending_event(db, session_id, event_id, actor.id):
        raise HTTPException(403, "只能删除自己本回合尚未推进的发言")
    session_service.set_turn_confirm(db, session_id, actor.id, False)
    room_hub.broadcast(session_id, _make_chunk("event_delete", event_id=event_id))
    room_hub.broadcast(
        session_id,
        _make_chunk("turn_state", metadata=session_service.live_turn_state(db, session_id, token)),
    )
    return {"ok": True}


@router.get("/{session_id}/search")
def search_history(
    session_id: str,
    q: str = "",
    limit: int = 20,
    offset: int = 0,
    order: str = "desc",
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    """在本局历史里模糊检索，返回匹配事件（含 sequence_num 供前端定位/跳转）。

    ``order``：desc 由新到旧（默认），asc 由旧到新。``total`` 供前端画分页。
    """
    require_session_viewer(db, session_id, token)
    rows, total = session_service.search_events(
        db, session_id, q, limit=limit, offset=offset, order=order,
    )
    return {
        "total": total,
        "offset": max(0, offset),
        "limit": max(1, limit),
        "results": [
            {
                "id": e.id,
                "sequence_num": e.sequence_num,
                "event_type": e.event_type,
                "actor_name": e.actor_name or "",
                # 片段以命中处为中心：一段旁白两三百字，关键词常落在后半截，
                # 取开头会切掉它，看着就像「这条不含关键词却被搜出来了」。
                "content": session_service.search_snippet(e.content or "", q),
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in rows
        ],
    }


@router.get("/{session_id}/locations")
def locations(
    session_id: str,
    char_id: str | None = None,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    """大地图/调查板：已知地点列表（含当前所在、相互连接、队友分布；未探索的不显示）。"""
    game_session = require_session_viewer(db, session_id, token)
    # get_module 会为迁移前的存量模组懒生成统一 map_nodes。
    module = module_service.get_module(db, game_session.module_id)
    if not module:
        raise HTTPException(404, "模组不存在")
    from app.services import hex_map
    hex_map.ensure_module_map(db, module)   # 存量模组沙盘坐标懒回填（幂等，有改动才落库）
    events = session_service.get_session_events(db, session_id)
    char_names = {c.id: c.name for c in session_service.get_party_members(db, session_id)}
    god_view = session_service.is_kp(db, session_id, token)   # 真人 KP 席位 → 上帝视角
    locations = session_service.list_known_locations(
            module, game_session, char_id=char_id, events=events, char_names=char_names,
            reveal_all=god_view,
        )
    return {
        "locations": locations,
        "map_nodes": session_service.list_visible_map_nodes(module, locations, reveal_all=god_view),
        "god_view": god_view,
        # 沙盘氛围底图：模组里生成的那张俯视图，局内大地图同样垫在网格之下。
        # 纯装饰、不参与方位与迷雾——地貌格本就全量下发（未发现的场景只清掉 scene_id、
        # 保留其地貌），所以底图的铺展范围在局内与模组页一致，不会随探索进度伸缩。
        "map_backdrop": str((module.world_setting or {}).get("sandbox_backdrop") or ""),
    }


@router.post("/{session_id}/travel")
async def travel(
    session_id: str,
    data: TravelRequest,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    """玩家经大地图『前往』某已知地点：确定性切换该玩家所在场景，再由 KP 叙述抵达见闻。

    场景切换由玩家显式发起（而非 KP 据只言片语臆测），杜绝「说句话就被自动搬走」。
    """
    game_session = db.get(GameSession, session_id)
    if not game_session:
        raise HTTPException(404, "会话不存在")
    if game_session.status != "active":
        raise HTTPException(400, "会话未处于活跃状态")
    if generation_manager.is_generating(session_id):
        raise HTTPException(409, "KP 正在叙事，请稍候")
    actor = require_session_actor(
        db, session_id, token, data.acting_character_id,
    )

    module = db.get(Module, game_session.module_id)
    scene_id = (data.scene_id or "").strip()
    events = session_service.get_session_events(db, session_id)
    known = session_service.known_scene_ids(module, game_session, events) if module else set()
    if scene_id not in known:
        raise HTTPException(400, "该地点尚未知晓或不可前往")
    cur = session_service.get_char_location(game_session, actor.id)
    if cur == scene_id:
        raise HTTPException(400, "你已身处该地点")

    def _sname(sid: str) -> str:
        s = next((x for x in (module.scenes or []) if x.get("id") == sid), None)
        return (s or {}).get("title") or (s or {}).get("name") or sid

    # 场景连通校验：目标须沿 connections 连通图可达，且**途经的场景必须是去过的**
    # （不相邻但沿已到访路径连通 → 允许，KP 叙述途经；否则拒绝）。
    # 只判图上连通会放行「取道从没去过的 2 号车厢抵达先头车厢」，而抵达叙述又写明
    # 「途经不停留、不触发事件」——合起来就是无视沿途的怪物凭空穿过去。
    # 模组没建图时 find_scene_path 返回平凡路径，行为与从前一致。
    path = session_service.find_scene_path(
        module, cur, scene_id, via_allowed=session_service.passable_scene_ids(game_session),
    )
    if path is None:
        blocker = session_service.travel_blocker(module, game_session, cur, scene_id)
        if blocker:
            bid, why = blocker
            raise HTTPException(400, f"去{_sname(scene_id)}要先经过{_sname(bid)}——{why}。")
        reachable = [
            _sname(n) for n in session_service.scene_neighbors(module, cur) if n in known
        ]
        hint = f"（由此可直达：{'、'.join(reachable)}）" if reachable else ""
        raise HTTPException(400, f"该地点与当前位置不连通，无法直接前往{hint}")
    via = [_sname(sid) for sid in path[1:-1]]

    scene_name = _sname(scene_id)

    if data.stash or game_session.kp_mode == "human":
        # 暂存模式：把「前往」作为本回合暂存动作加入（与发言同批）。位置的确定性同步 + 抵达叙述
        # 延到推进本回合时随 AI KP 或真人 KP 处理——不再单独触发一次生成、也不必手动点图。
        ev = session_service.add_event(
            db, session_id, "action", f"（前往：{scene_name}）",
            actor_id=actor.id, actor_name=actor.name,
            metadata={"pending_turn": True, "travel": True, "scene_id": scene_id},
        )
        room_hub.broadcast(session_id, event_to_chunk(ev))
        session_service.set_turn_confirm(db, session_id, actor.id, False)
        room_hub.broadcast(
            session_id,
            _make_chunk("turn_state", metadata=session_service.live_turn_state(db, session_id, token)),
        )
        return {"ok": True, "stashed": True}

    generation_manager.start(
        session_id, run_travel_generation(session_id, actor.id, scene_id, via=via),
    )
    return {"ok": True}
