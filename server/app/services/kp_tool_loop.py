"""KP 工具协议、原生 agent loop 与旧文本指令兼容运行时。"""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import AsyncIterator

from sqlalchemy.orm import Session

from app.ai import profile_store, turn_planner
from app.ai import tools as kp_tools
from app.ai.agents.kp_agent import _CHECK_TURN_TEMPERATURE, KPAgent
from app.ai.context import build_kp_context
from app.ai.prompts.kp_system import (
    KP_DICE_CONTINUATION_PROMPT,
    KP_MODULE_CONTINUATION_PROMPT,
    KP_RECALL_CONTINUATION_PROMPT,
    KP_RULE_CONTINUATION_PROMPT,
)
from app.ai.provider import ToolCall
from app.models.character import Character
from app.models.module import Module
from app.models.session import GameSession
from app.rules.registry import get_engine
from app.services import (
    chat_event_writer,
    command_protocol,
    dice_runtime,
    event_recall,
    illustration_service,
    kp_actions,
    module_rag_service,
    narration_protocol,
    npc_identity,
    rule_options_service,
    rulebook_service,
    session_service,
    team_turn_service,
    style_editor,
    turn_context,
    turn_effects,
)
from app.services.event_protocol import make_chunk as _make_chunk

logger = logging.getLogger(__name__)

DICE_CHECK_RE = command_protocol.DICE_CHECK_RE
OPPOSED_CHECK_RE = command_protocol.OPPOSED_CHECK_RE
SAN_CHECK_RE = command_protocol.SAN_CHECK_RE
HP_CHANGE_RE = command_protocol.HP_CHANGE_RE
NPC_ACT_RE = command_protocol.NPC_ACT_RE
SCENE_CHANGE_RE = command_protocol.SCENE_CHANGE_RE
TRAVEL_SUGGEST_RE = command_protocol.TRAVEL_SUGGEST_RE
BLOCK_PATH_RE = command_protocol.BLOCK_PATH_RE
UNBLOCK_PATH_RE = command_protocol.UNBLOCK_PATH_RE
RULE_LOOKUP_RE = command_protocol.RULE_LOOKUP_RE
MODULE_LOOKUP_RE = command_protocol.MODULE_LOOKUP_RE
RECALL_HISTORY_RE = command_protocol.RECALL_HISTORY_RE
SET_FLAG_RE = command_protocol.SET_FLAG_RE
CLEAR_FLAG_RE = command_protocol.CLEAR_FLAG_RE
HANDOUT_RE = command_protocol.HANDOUT_RE
MARK_SEEN_RE = command_protocol.MARK_SEEN_RE
GROUP_RE = command_protocol.GROUP_RE
MAX_RULE_LOOKUPS = command_protocol.MAX_RULE_LOOKUPS
MAX_DICE_CONTINUATIONS = command_protocol.MAX_DICE_CONTINUATIONS
_parse_tag_kv = command_protocol.parse_tag_kv
_filter_narration_stream = narration_protocol.filter_narration_stream
_is_party_speaker = narration_protocol._is_party_speaker
_record_chunk_event = chat_event_writer.record_chunk_event
_resolve_opposed = dice_runtime._resolve_opposed
_record_rag = turn_context._record_rag
_record_npc_say_memory = turn_context._record_npc_say_memory
_scene_name = turn_context._scene_name
_matcher_npcs = team_turn_service._matcher_npcs
_stream_narration_filtered = team_turn_service._stream_narration_filtered
_attach_npc_portrait = illustration_service._attach_npc_portrait
_attach_npc_portraits = illustration_service._attach_npc_portraits
_exec_npc_act = kp_actions._exec_npc_act
_exec_start_chase = kp_actions._exec_start_chase
_exec_start_combat = kp_actions._exec_start_combat
_exec_say = kp_actions._exec_say
_exec_san_check = turn_effects._exec_san_check
_exec_hp_change = turn_effects._exec_hp_change
_exec_dice_check = turn_effects._exec_dice_check
_exec_scene_change = turn_effects._exec_scene_change
_exec_flag = turn_effects._exec_flag
_exec_handout = turn_effects._exec_handout
_exec_mark_seen = turn_effects._exec_mark_seen
_travel_suggest_event = turn_effects.travel_suggest_event
_set_path_block = turn_effects.set_path_block


def _rule_lookup_passages(
    db: Session, query: str, rule_system: str, game_session: GameSession | None = None,
) -> str:
    """检索规则书原文并拼成回灌片段；检索不到给降级文案（fail-open）。"""
    hits = rulebook_service.retrieve(db, query, rule_system, k=3)
    _record_rag(db, game_session, kind="rule", mode="active", query=query, hits=hits)
    if hits:
        return "\n\n".join(f"[第 {h['page']} 页] {h['text']}" for h in hits)
    return "（未在规则书中找到直接匹配的内容，请依据《裁定手册》与你的经验处理。）"


def _recall_history_passages(
    db: Session, session_id: str, game_session: GameSession, query: str,
) -> str:
    """按语义把本局早前的事件原文找回来（fail-open：查不到给降级文案）。

    只在滚动摘要游标**之前**的事件里找：游标之后的事件本来就在上下文里逐字躺着，
    再检索一遍纯属浪费，还会把 KP 的注意力从眼前拉走。
    """
    cursor = (game_session.world_state or {}).get("story_summary_seq") or 0
    hits = event_recall.recall(
        db, session_id, query, k=3,
        scene_id=game_session.current_scene_id,
        before_seq=cursor or None,
    )
    return event_recall.format_recall(hits)


def _module_lookup_passages(
    db: Session, module: Module, game_session: GameSession, query: str,
) -> str:
    """检索模组原文并拼成回灌片段；检索不到/失败给降级文案（fail-open）。"""
    try:
        hits = module_rag_service.retrieve(
            db, module.id, query, k=3, scene_id=game_session.current_scene_id,
        )
    except Exception:  # noqa: BLE001 — 检索失败降级为无命中
        logger.exception("模组原文检索失败（已降级）：module=%s", module.id)
        hits = []
    _record_rag(db, game_session, kind="module", mode="active", query=query, hits=hits)
    if hits:
        return "\n\n".join(
            f"[片段 {i}] {h['text']}" for i, h in enumerate(hits, start=1)
        )
    return "（未在模组原文中找到直接匹配的内容，请依据结构化模组资料与你的经验续写。）"


# ── KP agent loop（tool use 新路径，use_tool_calls 开关控制）──────────────────

# 单轮 loop 的步数上限：超限注入「请直接收束」再生成一次收尾，防无限工具链。
MAX_TOOL_LOOP_STEPS = 6


def _tool_loop_active(llm) -> bool:
    """KP 生成是否走 agent loop（工具调用）新路径：配置开关 && Provider 支持工具。

    开关**默认开启**（use_tool_calls=true）；无激活档/读取异常一律回退旧路径（fail-open）。
    """
    try:
        profile = profile_store.load_active_profile()
    except Exception:
        return False
    if not profile or not getattr(profile, "use_tool_calls", True):
        return False
    try:
        return bool(llm.supports_tools())
    except Exception:
        return False


def _merge_step_result(result: list, step: list) -> None:
    """把一步生成的产物合并进整轮聚合 result（对话/分组偏移按已累计旁白长度平移）。"""
    base = len(result[0])
    result[0] += step[0]
    result[1] += step[1]
    if len(result) > 2 and len(step) > 2:
        result[2].extend(step[2])
    if len(result) > 3 and len(step) > 3:
        result[3].extend((off + base, spk, txt) for off, spk, txt in step[3])
    if len(result) > 4 and len(step) > 4:
        result[4].extend((off + base, label) for off, label in step[4])


# 裸值容错的单参数指令（[SET_FLAG hint_x] 这类漏写键名的旧习惯）→ 对应参数键
_SOLO_ARG_KEY = {
    "set_flag": "flag", "clear_flag": "flag", "handout": "id",
    "scene_change": "scene_id", "travel_suggest": "scene_id",
    "block_path": "scene_id", "unblock_path": "scene_id",
    "rule_lookup": "query", "module_lookup": "query", "recall_history": "query",
}

_TEXT_TAG_RE = re.compile(r"\[([A-Z_]{3,})(?:[:：\s]([^\]]*))?\]")


def _tool_call_from_text(step_text: str) -> ToolCall | None:
    """loop 兜底：模型没走工具、而是把指令写成了文本（手写 prompt 的旧习惯）——
    把第一条终止型指令解析成等价的合成 ToolCall，交给同一执行器处理。

    只认注册表里的终止型指令（GROUP/SAY 是文本标注不算动作）。
    参数解析与旧正则同款宽容：键值对优先，单参数指令允许裸值。
    """
    text = (step_text or "").replace("【", "[").replace("】", "]")
    for m in _TEXT_TAG_RE.finditer(text):
        tag, inner = m.group(1), (m.group(2) or "").strip()
        # SAY/GROUP 是文本标注、不是动作：内联 [SAY] 由台词过滤器直接抽成气泡，
        # 这里绝不能再合成一个 say() 工具调用，否则同一句台词会重复出气泡。
        if tag in ("SAY", "GROUP"):
            continue
        name = kp_tools.TAG_TO_TOOL.get(tag)
        if name is None:
            continue
        kv = _parse_tag_kv(inner)
        if not kv and inner:
            solo = _SOLO_ARG_KEY.get(name)
            if solo:
                kv = {solo: inner}
        return ToolCall(id=f"text_{uuid.uuid4().hex[:8]}", name=name, arguments=kv)
    return None


def _plan_check_call(plan: turn_planner.TurnPlan) -> ToolCall:
    """裁定轮兜底：按计划的 check 字段拼出确定性补掷的 dice_check 调用
    （与 turn_planner._check_directive 同一语义，等价 KPAgent 的补指令兜底）。"""
    check = plan.check
    args: dict = {"skill": check.skill or "侦查"}
    if check.difficulty:
        args["difficulty"] = check.difficulty
    if check.visibility and check.visibility != "open":
        args["visibility"] = check.visibility
    if check.chars:
        args["chars"] = check.chars
    return ToolCall(id=f"fallback_{uuid.uuid4().hex[:8]}", name="dice_check", arguments=args)


# 工具名 → 这一步之后玩家该看到的等待说明。措辞按玩家视角写，不出现工具名/字段名。
_STEP_NOTES: dict[str, str] = {
    "dice_check": "守秘人正在结算这次检定…",
    "opposed_check": "守秘人正在结算这次对抗…",
    "san_check": "守秘人正在结算理智…",
    "hp_change": "守秘人正在结算伤势…",
    "start_combat": "战斗即将开始…",
    "start_chase": "追逐即将开始…",
    "scene_change": "守秘人正在带你们前往新的地方…",
    "travel_suggest": "守秘人正在指一条路…",
    "block_path": "守秘人正在确认这条路通不通…",
    "unblock_path": "守秘人正在确认这条路通不通…",
    "handout": "守秘人正在准备要给你们的东西…",
    "rule_lookup": "守秘人正在翻阅规则书…",
    "module_lookup": "守秘人正在翻阅剧本…",
    "recall_history": "守秘人正在回想早前的事…",
    "set_flag": "守秘人正在推进剧情…",
    "clear_flag": "守秘人正在推进剧情…",
    # say / npc_act 不单列：它们之后紧接着往往就是正文，用通用措辞即可。
}
_STEP_NOTE_DEFAULT = "守秘人正在继续…"


def _step_note(tool_calls: list[ToolCall]) -> str:
    """本步执行了哪些工具 → 一句给玩家看的等待说明。

    多个工具时取第一个有专门措辞的；都没有则用通用兜底。
    """
    for tc in tool_calls:
        note = _STEP_NOTES.get(tc.name)
        if note:
            return note
    return _STEP_NOTE_DEFAULT


def _build_kp_tool_executor(
    db: Session, session_id: str, game_session: GameSession, module: Module,
    player_char: Character, teammates: list[Character] | None, llm,
    result: list,
):
    """构建 loop 路径的工具执行器：把注册表工具名分发到上面的共用执行函数（不复制逻辑）。

    闭包内维护每轮查阅配额（rule_lookup 与 module_lookup 合计 MAX_RULE_LOOKUPS 次，
    超限返回拒绝文本——与旧路径 lookup_depth 语义一致）。未知工具名回「无此工具」结果、
    任何执行异常 fail-open 返回错误说明，绝不断流。
    """
    lookup_used = 0

    # 每个工具一个 handler(name, kv)；分发表取代旧的长 if/elif 链。ToolSpec（tools.py）仍是
    # 参数 schema 的单一事实源；此处是「工具名 → 执行」的单一事实源（独立放置以避免与
    # tools.py 循环依赖，handler 需闭包访问 db/session/module 等运行期上下文）。
    async def _h_dice_check(name, kv):
        if not kv.get("skill"):
            return kp_tools.ToolOutcome("参数缺失：skill 为必填。请带上技能名重试，或直接继续叙述。")
        chunks, descs, pending = await _exec_dice_check(
            db, session_id, game_session, module, kv, player_char, teammates,
        )
        if pending:
            return kp_tools.ToolOutcome(
                "已向该玩家发出检定请求，等待其亲自掷骰。本轮叙述就此收束，绝不预测结果。",
                chunks=chunks, suspend=True,
            )
        return kp_tools.ToolOutcome(
            KP_DICE_CONTINUATION_PROMPT.format(dice_results="\n".join(descs)), chunks=chunks,
        )

    async def _h_opposed_check(name, kv):
        descs: list[str] = []
        chunks = [
            c async for c in _resolve_opposed(
                db, session_id, kv, get_engine(module.rule_system),
                module, player_char, teammates, descs,
            )
        ]
        if not descs:
            return kp_tools.ToolOutcome("参数缺失：skill（或 a_skill/b_skill）为必填。", chunks=chunks)
        return kp_tools.ToolOutcome(
            KP_DICE_CONTINUATION_PROMPT.format(dice_results="\n".join(descs)), chunks=chunks,
        )

    async def _h_san_check(name, kv):
        chunks, descs, pending = await _exec_san_check(
            db, session_id, game_session, kv, player_char, teammates,
        )
        if pending:
            return kp_tools.ToolOutcome(
                "已向真人目睹者发出理智检定请求，等待其亲自掷骰。本轮叙述就此收束。",
                chunks=chunks,
                suspend=True,
            )
        if not descs:
            return kp_tools.ToolOutcome(
                "本次理智检定无需结算（目睹者均已对该恐怖源检定过）。", chunks=chunks,
            )
        return kp_tools.ToolOutcome(
            KP_DICE_CONTINUATION_PROMPT.format(dice_results="\n".join(descs)), chunks=chunks,
        )

    async def _h_lookup(name, kv):
        nonlocal lookup_used
        if lookup_used >= MAX_RULE_LOOKUPS:
            return kp_tools.ToolOutcome(
                f"本轮查阅配额已用完（规则书与模组原文合计最多 {MAX_RULE_LOOKUPS} 次），"
                "请依据既有资料直接续写，不要再查阅。"
            )
        query = kv.get("query", "").strip()
        if not query:
            return kp_tools.ToolOutcome("参数缺失：query 为必填。")
        lookup_used += 1
        if name == "rule_lookup":
            passages = _rule_lookup_passages(db, query, module.rule_system, game_session)
            return kp_tools.ToolOutcome(
                KP_RULE_CONTINUATION_PROMPT.format(query=query, passages=passages),
                chunks=[_make_chunk("system", "守秘人翻阅规则书……")],
            )
        if name == "recall_history":
            passages = _recall_history_passages(db, session_id, game_session, query)
            return kp_tools.ToolOutcome(
                KP_RECALL_CONTINUATION_PROMPT.format(query=query, passages=passages),
                chunks=[_make_chunk("system", "守秘人回想早前的事……")],
            )
        passages = _module_lookup_passages(db, module, game_session, query)
        return kp_tools.ToolOutcome(
            KP_MODULE_CONTINUATION_PROMPT.format(query=query, passages=passages),
            chunks=[_make_chunk("system", "守秘人翻阅模组手稿……")],
        )

    async def _h_say(name, kv):
        who = kv.get("who", "").strip()
        text = kv.get("text", "").strip().strip("“”\"「」『』")
        if not who or not text:
            return kp_tools.ToolOutcome("参数缺失：who 与 text 均为必填。")
        # 守卫：绝不用 say() 替玩家/队友说话或行动（他们的台词由本人给出）。
        party = {player_char.name} | {t.name for t in (teammates or [])}
        if _is_party_speaker(who, party):
            return kp_tools.ToolOutcome(
                f"拒绝：{who} 是玩家或队友角色，你不能替他们说话或行动。"
                "玩家与队友的言行只能由他们本人给出；你只叙述 NPC 与环境，把选择权留给他们。"
            )
        chunks = _exec_say(
            result, module, who, text,
            masker=npc_identity.build_masker(db, session_id, module),
        )
        return kp_tools.ToolOutcome(
            "台词已作为气泡展示给玩家（续写时不要复述这句话）。", chunks=chunks,
        )

    async def _h_start_combat(name, kv):
        chunks = await _exec_start_combat(
            db, session_id, game_session, module, player_char, teammates, llm,
            kv.get("enemies", ""), kv.get("trigger", ""),
        )
        return kp_tools.ToolOutcome(
            "已切入结构化战斗轮，交由系统按先攻推进；本轮就此收束，战斗结束后系统会回灌结果摘要。",
            chunks=chunks, suspend=True,
        )

    async def _h_start_chase(name, kv):
        chunks = _exec_start_chase(
            db, session_id, module, player_char, kv.get("pursuer", ""), kv.get("trigger", ""),
        )
        return kp_tools.ToolOutcome(
            "已切入追逐（抽象距离轨），交由系统逐轮推进；本轮就此收束，追逐结束后系统会回灌结果。",
            chunks=chunks, suspend=True,
        )

    async def _h_npc_act(name, kv):
        npc_id = kv.get("npc_id", "").strip()
        trigger = kv.get("trigger", "").strip()
        if not npc_id or not trigger:
            return kp_tools.ToolOutcome("参数缺失：npc_id 与 trigger 均为必填。")
        chunks, response = await _exec_npc_act(
            db, session_id, game_session, module, llm, player_char, npc_id, trigger,
        )
        return kp_tools.ToolOutcome(
            f"该 NPC 已行动/开口（台词已直接展示给玩家，续写时不要复述）：{response}", chunks=chunks,
        )

    async def _h_scene_change(name, kv):
        chunks, sid, note = await _exec_scene_change(
            db, session_id, game_session, module,
            kv.get("scene_id", "").strip(), player_char, teammates,
        )
        if sid:
            return kp_tools.ToolOutcome(f"ok：场景已切换至 {_scene_name(module, sid)}", chunks=chunks)
        return kp_tools.ToolOutcome(note or "场景引用无法解析或未变化（保持当前场景）。", chunks=chunks)

    async def _h_flag(name, kv):
        flag = kv.get("flag", "").strip()
        if not flag:
            return kp_tools.ToolOutcome("参数缺失：flag 为必填。")
        chunks = _exec_flag(db, session_id, game_session, flag, name == "set_flag")
        return kp_tools.ToolOutcome("ok", chunks=chunks)

    async def _h_hp_change(name, kv):
        chunks = await _exec_hp_change(
            db, session_id, player_char,
            kv.get("target", ""), kv.get("delta", ""), kv.get("reason", ""),
            module=module, teammates=teammates,
        )
        if chunks:
            return kp_tools.ToolOutcome("ok", chunks=chunks)
        return kp_tools.ToolOutcome("未结算（target 当前仅支持 player，且 delta 须为整数）。")

    async def _h_travel_suggest(name, kv):
        ref = (kv.get("scene_id") or "").strip()
        if not ref:
            return kp_tools.ToolOutcome("参数缺失：scene_id 为必填。")
        chunks, note = _travel_suggest_event(
            db, session_id, game_session, module, ref, kv.get("reason", ""),
        )
        return kp_tools.ToolOutcome(note, chunks=chunks)

    async def _h_block_path(name, kv):
        ref = (kv.get("scene_id") or "").strip()
        if not ref:
            return kp_tools.ToolOutcome("参数缺失：scene_id 为必填。")
        _chunks, note = _set_path_block(
            db, session_id, game_session, module, ref, kv.get("reason", ""),
            blocked=(name == "block_path"),
        )
        return kp_tools.ToolOutcome(note)

    async def _h_handout(name, kv):
        hid = kv.get("id", "").strip()
        if not hid:
            return kp_tools.ToolOutcome("参数缺失：id 为必填。")
        chunks, note = await _exec_handout(
            db, session_id, game_session, module, hid, player_char, teammates,
        )
        return kp_tools.ToolOutcome(note, chunks=chunks)

    async def _h_mark_seen(name, kv):
        # 纯记账，不出 chunk：玩家看不到台账，只会感到 KP 不再重复同一个发现桥段。
        note = _exec_mark_seen(
            db, session_id, game_session, module, kv, player_char, teammates,
        )
        return kp_tools.ToolOutcome(note)

    handlers = {
        "dice_check": _h_dice_check,
        "opposed_check": _h_opposed_check,
        "san_check": _h_san_check,
        "rule_lookup": _h_lookup,
        "module_lookup": _h_lookup,
        "recall_history": _h_lookup,
        "say": _h_say,
        "start_combat": _h_start_combat,
        "start_chase": _h_start_chase,
        "npc_act": _h_npc_act,
        "scene_change": _h_scene_change,
        "set_flag": _h_flag,
        "clear_flag": _h_flag,
        "hp_change": _h_hp_change,
        "travel_suggest": _h_travel_suggest,
        "block_path": _h_block_path,
        "unblock_path": _h_block_path,
        "handout": _h_handout,
        "mark_seen": _h_mark_seen,
    }

    async def execute(call: ToolCall) -> kp_tools.ToolOutcome:
        name = call.name
        kv = {k: str(v).strip() for k, v in (call.arguments or {}).items() if v is not None}
        if kp_tools.TOOLS_BY_NAME.get(name) is None:
            return kp_tools.ToolOutcome(
                f"无此工具：{name}。只可调用系统提供的工具；若无需工具，直接继续叙述。"
            )
        handler = handlers.get(name)
        if handler is None:
            return kp_tools.ToolOutcome(
                f"工具 {name} 暂无 loop 行为（内部错误），请直接继续叙述。"
            )
        try:
            return await handler(name, kv)
        except Exception:
            logger.exception("工具执行失败: %s session=%s", name, session_id)
            return kp_tools.ToolOutcome(
                f"工具 {name} 执行出错，请不要重试该工具，直接继续叙述。"
            )

    execute._handled_tools = frozenset(handlers)   # 供测试校验：分发表须覆盖注册表全部工具
    return execute


async def _run_kp_agent_loop(
    llm, messages: list[dict], result: list, execute_tool, *,
    tools: list[dict] | None = None,
    npcs: list[dict] | None = None,
    group_label: str | None = None,
    plan: turn_planner.TurnPlan | None = None,
    max_steps: int = MAX_TOOL_LOOP_STEPS,
    party_names: set[str] | None = None,
    shown_dialogues: list[str] | None = None,
    event_order: list | None = None,
) -> AsyncIterator[str]:
    """KP agent loop：与 _stream_narration_filtered 并列的新路径（use_tool_calls 开启时用）。

    stream_chat 流式生成：文本增量过同一套台词过滤后实时广播；tool_call 到达 →
    执行器执行 → 结果作为 role="tool" 消息回注 → 继续循环。DICE_CHECK/RULE_LOOKUP/
    MODULE_LOOKUP 由此天然取代旧路径的「续写 prompt」模式。

    - 步数上限 max_steps（默认 6）：超限注入「请直接收束本轮叙述」再无工具生成一次收尾；
    - 裁定轮（plan.requires_check）：采样降温 _CHECK_TURN_TEMPERATURE，且若模型始终没
      发起 dice_check/opposed_check，则按计划确定性补掷（等价 KPAgent 的补指令兜底）；
    - 模型把指令写成文本时（手写 prompt 旧习惯）：解析成合成 ToolCall 走同一执行器，
      [MOVE] 内联标记照旧生效，[SAY]/[GROUP] 由文本过滤器照常处理；
    - 执行器返回 suspend（如已挂「待玩家投骰」）：本轮生成就此收束。
    产物写入 result（与旧路径同构）；validator 终检、落库、世界记忆钩子、幕后推演等
    收尾由调用方与旧路径共用——loop 只替换「生成 + 指令执行」段。
    """
    tools = tools if tools is not None else kp_tools.openai_tool_schemas()
    requires_check = bool(plan is not None and plan.requires_check)
    temperature = _CHECK_TURN_TEMPERATURE if requires_check else 0.85
    messages = list(messages)  # loop 会往里回注消息，不污染调用方的列表
    did_check = False
    natural_end = False
    # 工具轮次与用到的工具：这一段现已是整个回合最贵的部分（实测一轮 5 次调用、输入 57.1k）。
    # 轮次是乘数——每轮都要把累积消息整份重发、模型每轮再思考一遍。不记下「几轮、哪些工具」，
    # 就只能看到「KP 叙事 95.7s」而不知道是谁把它拖长的。
    used_tools: list[str] = []

    def _log_rounds() -> None:
        if used_tools:
            logger.info("耗时|KP 工具循环 %d 轮：%s", len(used_tools), "、".join(used_tools))

    for _step in range(max_steps):
        step = ["", "", [], [], []]
        tool_calls: list[ToolCall] = []
        reasoning_parts: list[str] = []

        async def _text_deltas(calls=tool_calls, reasoning=reasoning_parts):
            async for delta in llm.stream_chat(messages, tools=tools, temperature=temperature):
                if delta.kind == "text" and delta.text:
                    yield delta.text
                elif delta.kind == "reasoning" and delta.text:
                    reasoning.append(delta.text)
                elif delta.kind == "tool_call" and delta.tool_call is not None:
                    calls.append(delta.tool_call)

        try:
            async for chunk in _filter_narration_stream(
                _text_deltas(), step, npcs=npcs, group_label=group_label,
                guess_speakers=False,  # 对话走 say() 工具；旁白里的裸引号一律留旁白、不猜
                party_names=party_names,  # 内联 [SAY] 误代言玩家/队友也挡下
                shown_dialogues=shown_dialogues,
                prior_narration=result[0],
            ):
                yield chunk
        except BaseException:
            _merge_step_result(result, step)  # 断流也保住已生成片段（调用方负责落库）
            raise
        _merge_step_result(result, step)

        step_text = (step[1] or "").replace("【", "[").replace("】", "]")
        if not tool_calls:
            synthetic = _tool_call_from_text(step_text)
            if synthetic is not None:
                tool_calls = [synthetic]
        if not tool_calls and requires_check and not did_check:
            # 裁定轮兜底：既没调工具也没写指令 → 确定性补掷计划指定的检定
            tool_calls = [_plan_check_call(plan)]
        if not tool_calls:
            natural_end = True
            break

        used_tools.extend(tc.name for tc in tool_calls)
        assistant_message = {
            "role": "assistant",
            "content": step[1] or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments or {}, ensure_ascii=False),
                    },
                }
                for tc in tool_calls
            ],
        }
        if reasoning_parts:
            # DeepSeek 思考模型要求工具续接请求保留产生 tool_calls 时的完整思考内容；
            # 其他 OpenAI 兼容供应商没有该字段时保持原消息格式。
            assistant_message["reasoning_content"] = "".join(reasoning_parts)
        messages.append(assistant_message)
        suspended = False
        for tc in tool_calls:
            if tc.name in ("dice_check", "opposed_check"):
                did_check = True
            outcome = await execute_tool(tc)
            for chunk in outcome.chunks:
                # 记录该工具事件在广播时的旁白偏移，供收尾把它重排回旁白中的正确位置
                if event_order is not None:
                    _record_chunk_event(event_order, chunk, len(result[0]))
                yield chunk
            messages.append({
                "role": "tool", "tool_call_id": tc.id, "content": outcome.result_text,
            })
            if outcome.suspend:
                suspended = True
        if suspended:
            _log_rounds()
            return
        # 调过工具就必须再跑一轮，哪怕只是为了确认「没有更多工具了」——那一轮往往几乎不产
        # 文本，屏幕上只剩一个不动的脉冲点。这里说明一句正在做什么；下一轮一有正文到达，
        # 前端会自行清掉这行字（见 GameSessionPage 对 narration 增量的处理）。
        yield _make_chunk("housekeeping", _step_note(tool_calls))

    _log_rounds()
    if not natural_end:
        # 步数超限：注入收束指令，最后一次不带工具生成收尾
        messages.append({
            "role": "system",
            "content": (
                "（工具调用步数已达上限）请不要再调用任何工具、也不要输出任何指令，"
                "直接收束本轮叙述：自然交代当前处境，停在等待玩家行动处。"
            ),
        })
        step = ["", "", [], [], []]

        async def _plain_deltas():
            async for delta in llm.stream_chat(messages, tools=None, temperature=temperature):
                if delta.kind == "text" and delta.text:
                    yield delta.text

        try:
            async for chunk in _filter_narration_stream(
                _plain_deltas(), step, npcs=npcs, group_label=group_label,
                guess_speakers=False, party_names=party_names,
                shown_dialogues=shown_dialogues,
                prior_narration=result[0],
            ):
                yield chunk
        except BaseException:
            _merge_step_result(result, step)
            raise
        _merge_step_result(result, step)


def _group_label_for_text(groups: list[dict] | None, text: str) -> str | None:
    """在分头分组里找出「这段文字归属的组」：按成员名做包含匹配。"""
    if not groups:
        return None
    for grp in groups:
        for member in (grp.get("members") or []):
            if member and (member in (text or "") or (text or "") in member):
                return grp["label"]
    return groups[0]["label"] if groups else None


def _group_scope_resolver(
    kp_text: str,
    scene_groups: list[dict] | None,
    player_char: Character,
    teammates: list[Character] | None,
    focus_group_label: str | None = None,
):
    """返回 ``pos -> 该处指令所属分组的角色列表``（非分头时恒为 None）。

    分头行动逐组生成、合并成一段文本统一处理，指令本身不带出处。``_run_split_narrations``
    在每组产物前留了 ``[GROUP: scene=<组名>]`` 标记，这里按指令在文本中的位置回溯到最近的
    标记，从而知道这条 [SAN_CHECK]/[DICE_CHECK] 是哪一组的事——不这么定位，一组撞见的恐怖
    会照着「主角在哪」结算到另一组头上。
    """
    if not scene_groups or len(scene_groups) < 2:
        return lambda _pos: None

    party = [player_char] + list(teammates or [])

    def members_of(label: str | None) -> list[Character] | None:
        grp = next((g for g in scene_groups if g.get("label") == label), None)
        if grp is None:
            return None
        names = grp.get("members") or []
        return [
            c for c in party
            if any(n and (c.name == n or n in c.name or c.name in n) for n in names)
        ] or None

    marks = [
        (match.start(), (match.group(1) or "").split("=", 1)[-1].strip())
        for match in GROUP_RE.finditer(kp_text or "")
    ]

    def resolve(pos: int) -> list[Character] | None:
        label = next(
            (mark_label for mark_pos, mark_label in reversed(marks) if mark_pos <= pos),
            None,
        )
        return members_of(label) or members_of(focus_group_label)

    return resolve


async def _process_commands(
    db: Session,
    session_id: str,
    kp_text: str,
    module: Module,
    player_char: Character,
    game_session: GameSession,
    llm,
    teammates: list[Character] | None = None,
    allow_rule_lookup: bool = True,
    lookup_depth: int = 0,
    dice_depth: int = 0,
    scene_groups: list[dict] | None = None,
    focus_group_label: str | None = None,
    pre_gen_seq: int | None = None,
) -> AsyncIterator[str]:
    # 全角括号归一为半角：模型有时用【】写指令，统一成 [] 好让下面各指令正则命中并处理（而非泄漏）。
    kp_text = (kp_text or "").replace("【", "[").replace("】", "]")
    # 规则书查阅是终止性指令（独占一次回复的最后一行）：命中即查阅+续写，不再处理本段其余
    if allow_rule_lookup and lookup_depth < MAX_RULE_LOOKUPS:
        lookup = RULE_LOOKUP_RE.search(kp_text)
        if lookup:
            async for chunk in _handle_rule_lookup(
                db, session_id, lookup.group(1).strip(), module, player_char,
                game_session, llm, teammates=teammates, lookup_depth=lookup_depth,
            ):
                yield chunk
            return

    # 模组原文查阅同为终止性指令，与规则书查阅走同一开关与配额（lookup_depth 合并计数）
    if allow_rule_lookup and lookup_depth < MAX_RULE_LOOKUPS:
        mlookup = MODULE_LOOKUP_RE.search(kp_text)
        if mlookup:
            async for chunk in _handle_module_lookup(
                db, session_id, mlookup.group(1).strip(), module, player_char,
                game_session, llm, teammates=teammates, lookup_depth=lookup_depth,
            ):
                yield chunk
            return

    # 回想本局往事：同为终止性指令，共享同一开关与配额
    if allow_rule_lookup and lookup_depth < MAX_RULE_LOOKUPS:
        rlookup = RECALL_HISTORY_RE.search(kp_text)
        if rlookup:
            async for chunk in _handle_recall_history(
                db, session_id, rlookup.group(1).strip(), module, player_char,
                game_session, llm, teammates=teammates, lookup_depth=lookup_depth,
            ):
                yield chunk
            return

    dice_descriptions: list[str] = []
    san_pending = False
    dice_pending = False
    # 分头行动：按指令在合并文本里的位置回溯它出自哪一组，据此限定群体检定的候选域。
    group_scope = _group_scope_resolver(
        kp_text, scene_groups, player_char, teammates, focus_group_label,
    )

    for match in SAN_CHECK_RE.finditer(kp_text):
        kv = _parse_tag_kv(match.group(1))
        san_chunks, san_descs, pending = await _exec_san_check(
            db, session_id, game_session, kv, player_char, teammates,
            scope=group_scope(match.start()), pre_gen_seq=pre_gen_seq,
        )
        for chunk in san_chunks:
            yield chunk
        dice_descriptions.extend(san_descs)
        san_pending = san_pending or pending

    if san_pending:
        return

    for match in HP_CHANGE_RE.finditer(kp_text):
        hp_chunks = await _exec_hp_change(
            db, session_id, player_char,
            match.group(1).strip(), match.group(2).strip(), match.group(3).strip(),
            module=module, teammates=teammates,
        )
        for chunk in hp_chunks:
            yield chunk

    engine = get_engine(module.rule_system)

    for match in DICE_CHECK_RE.finditer(kp_text):
        kv = _parse_tag_kv(match.group(1))
        dice_chunks, dice_descs, pending = await _exec_dice_check(
            db, session_id, game_session, module, kv, player_char, teammates,
            scope=group_scope(match.start()),
        )
        for chunk in dice_chunks:
            yield chunk
        dice_descriptions.extend(dice_descs)
        dice_pending = dice_pending or pending

    if dice_pending:
        return

    for match in OPPOSED_CHECK_RE.finditer(kp_text):
        try:
            async for chunk in _resolve_opposed(
                db, session_id, _parse_tag_kv(match.group(1)),
                engine, module, player_char, teammates, dice_descriptions,
            ):
                yield chunk
        except ValueError as error:
            # 模型生成的内部指令可能缺字段；跳过坏指令，不中断已经生成的叙事。
            logger.warning("跳过无效的 AI 对抗检定指令：%s", error)

    if dice_descriptions:
        continuation_prompt = KP_DICE_CONTINUATION_PROMPT.format(
            dice_results="\n".join(dice_descriptions)
        )
        events = session_service.get_session_events(db, session_id)

        if scene_groups and len(scene_groups) >= 2:
            # 分头行动：骰子续写同样要按组生成、按组打 GROUP 标签，不能退化成单场景
            # 只演检定执行者那一列、丢掉其他组的后续剧情。
            # 这里只续写**实际产生检定结果的组**——其余组的场景叙事已由本轮分头生成覆盖，
            # 不需要再额外推一轮，避免其他场景被重复推进。
            by_group: dict[str, list[str]] = {}
            for desc in dice_descriptions:
                label = _group_label_for_text(scene_groups, desc)
                if label is None:
                    label = focus_group_label or scene_groups[0]["label"]
                by_group.setdefault(label, []).append(desc)
            if focus_group_label and not by_group:
                by_group[focus_group_label] = list(dice_descriptions)
            cont_command_parts: list[str] = []
            kp = KPAgent(llm)
            for label, group_descs in by_group.items():
                grp = next(g for g in scene_groups if g["label"] == label)
                messages = build_kp_context(
                    game_session, module, player_char, events, teammates=teammates,
                    viewer_scene_id=grp.get("scene_id"),
                    scene_groups=scene_groups,
                    rule_options_block=rule_options_service.context_block(db, game_session),
                )
                messages.append({
                    "role": "user",
                    "content": KP_DICE_CONTINUATION_PROMPT.format(
                        dice_results="\n".join(group_descs)
                    ),
                })
                cont_result = ["", "", [], [], []]
                try:
                    async for chunk in _stream_narration_filtered(
                        kp, messages, cont_result,
                        npcs=_matcher_npcs(module, teammates, game_session),
                        group_label=label,
                    ):
                        yield chunk
                    await style_editor.polish_result(llm, cont_result)   # 取消时不走，finally 落原文
                finally:
                    chat_event_writer.persist_narration(
                        db, session_id, cont_result,
                        attach_npc_portraits=_attach_npc_portraits,
                        model=chat_event_writer.model_name(llm),
                    )
                _record_npc_say_memory(
                    db, session_id, game_session, module, cont_result[2], grp["members"],
                )
                # 同 _run_split_narrations：留组界标记，续写里追加的 [SAN_CHECK]
                # （读懂禁忌知识那种）才不会顺着主角的位置结算到别组头上。
                cont_command_parts.append(f"[GROUP: scene={label}]\n{cont_result[1]}")
            cont_command_text = "\n".join(cont_command_parts)
        else:
            messages = build_kp_context(
                game_session, module, player_char, events, teammates=teammates,
                rule_options_block=rule_options_service.context_block(db, game_session),
            )
            messages.append({"role": "user", "content": continuation_prompt})

            kp = KPAgent(llm)
            cont_result = ["", "", [], [], []]
            try:
                async for chunk in _stream_narration_filtered(
                    kp, messages, cont_result, npcs=_matcher_npcs(module, teammates, game_session),
                ):
                    yield chunk
                await style_editor.polish_result(llm, cont_result)   # 取消时不走，finally 落原文
            finally:
                cont_narration = cont_result[0].rstrip()
                if cont_narration:
                    session_service.add_event(
                        db, session_id, "narration", cont_narration, actor_name="KP",
                        metadata=chat_event_writer.model_meta(llm),
                    )
                for npc_name, dialogue_text in cont_result[2]:
                    ev = session_service.add_event(
                        db, session_id, "dialogue", dialogue_text, actor_name=npc_name,
                    )
                    _attach_npc_portrait(db, session_id, module, ev)
            # 世界记忆钩子 c：续写里的 NPC 台词同样记入其互动史
            _record_npc_say_memory(
                db, session_id, game_session, module, cont_result[2],
                [player_char.name] + [t.name for t in (teammates or [])],
            )
            cont_command_text = cont_result[1]

        # 续写里 KP 可能再发指令（如读懂禁忌知识后追加 [SAN_CHECK]、或场景切换）——
        # 继续处理，但限深度防无限掷骰链。
        if dice_depth + 1 < MAX_DICE_CONTINUATIONS:
            async for chunk in _process_commands(
                db, session_id, cont_command_text, module, player_char, game_session, llm,
                teammates=teammates, allow_rule_lookup=False, dice_depth=dice_depth + 1,
                scene_groups=scene_groups,
                focus_group_label=focus_group_label,
                pre_gen_seq=pre_gen_seq,
            ):
                yield chunk

    for match in SCENE_CHANGE_RE.finditer(kp_text):
        scene_chunks, _sid, _note = await _exec_scene_change(
            db, session_id, game_session, module, match.group(1).strip(),
            player_char, teammates,
        )
        for chunk in scene_chunks:
            yield chunk

    # 此路不通 / 恢复通行：只改寻路看的状态，不搬人、不出卡片。
    for match in BLOCK_PATH_RE.finditer(kp_text):
        kv = _parse_tag_kv(match.group(1))
        _set_path_block(
            db, session_id, game_session, module,
            kv.get("scene_id") or match.group(1).strip(), kv.get("reason", ""), blocked=True,
        )
    for match in UNBLOCK_PATH_RE.finditer(kp_text):
        kv = _parse_tag_kv(match.group(1))
        _set_path_block(
            db, session_id, game_session, module,
            kv.get("scene_id") or match.group(1).strip(), "", blocked=False,
        )

    # 建议前往：不搬人，只挂一张「要不要去」的卡；玩家点了才进他自己的暂存动作。
    for match in TRAVEL_SUGGEST_RE.finditer(kp_text):
        kv = _parse_tag_kv(match.group(1))
        suggest_chunks, _note = _travel_suggest_event(
            db, session_id, game_session, module,
            kv.get("scene_id") or match.group(1).strip(), kv.get("reason", ""),
        )
        for chunk in suggest_chunks:
            yield chunk

    # 剧情状态推进：置/清标志后，刷新内存里的 game_session.world_state，使本次生成的后续
    # 处理（续写、NPC 行动）与下一轮上下文都能看到最新状态。
    for match in SET_FLAG_RE.finditer(kp_text):
        for chunk in _exec_flag(db, session_id, game_session, match.group(1).strip(), True):
            yield chunk
    for match in CLEAR_FLAG_RE.finditer(kp_text):
        for chunk in _exec_flag(db, session_id, game_session, match.group(1).strip(), False):
            yield chunk

    # 手书发放：[HANDOUT: id=xxx] → 把模组手书的原文落库为 system 事件并广播（handout 是
    # 给玩家看的实体文书，正常进聊天流，前端按 metadata.kind 渲染成信笺卡片）。
    # 幂等：同 id 只发放一次（重复发放静默跳过）；未知 id 静默跳过（只记日志，不出卡片）。
    for match in HANDOUT_RE.finditer(kp_text):
        inner = match.group(1).strip()
        hid = (_parse_tag_kv(inner).get("id") or inner).strip()
        handout_chunks, _note = await _exec_handout(
            db, session_id, game_session, module, hid, player_char, teammates,
        )
        for chunk in handout_chunks:
            yield chunk

    # 记账：[MARK_SEEN: clue=xxx] / [MARK_SEEN: event=机制点原文] → 只写世界记忆，不出 chunk。
    for match in MARK_SEEN_RE.finditer(kp_text):
        _exec_mark_seen(
            db, session_id, game_session, module,
            _parse_tag_kv(match.group(1).strip()), player_char, teammates,
        )

    for match in NPC_ACT_RE.finditer(kp_text):
        npc_chunks, _resp = await _exec_npc_act(
            db, session_id, game_session, module, llm, player_char,
            match.group(1).strip(), match.group(2).strip(),
        )
        for chunk in npc_chunks:
            yield chunk


async def _handle_rule_lookup(
    db: Session,
    session_id: str,
    query: str,
    module: Module,
    player_char: Character,
    game_session: GameSession,
    llm,
    teammates: list[Character] | None = None,
    lookup_depth: int = 0,
) -> AsyncIterator[str]:
    """KP 发起 [RULE_LOOKUP] 后：检索规则书原文 → 回灌让 KP 据此续写裁定。

    透明提示一条 ephemeral system（不落库）；检索不到时给降级文案让 KP 凭经验处理。
    续写产物再过一遍 _process_commands（禁再查阅），以便"查完规则随即发起检定"成立。
    """
    yield _make_chunk("system", "守秘人翻阅规则书……")

    passages = _rule_lookup_passages(db, query, module.rule_system, game_session)
    continuation = KP_RULE_CONTINUATION_PROMPT.format(query=query, passages=passages)
    events = session_service.get_session_events(db, session_id)
    messages = build_kp_context(
        game_session, module, player_char, events, teammates=teammates,
        rules_lookup_enabled=False,  # 续写阶段不再广告查阅，避免长链
    )
    messages.append({"role": "user", "content": continuation})

    kp = KPAgent(llm)
    cont_result = ["", "", [], [], []]
    try:
        async for chunk in _stream_narration_filtered(
            kp, messages, cont_result, npcs=_matcher_npcs(module, teammates, game_session),
        ):
            yield chunk
    finally:
        cont_narration = cont_result[0].rstrip()
        if cont_narration:
            session_service.add_event(
                db, session_id, "narration", cont_narration, actor_name="KP",
            )
        for npc_name, dialogue_text in cont_result[2]:
            ev = session_service.add_event(
                db, session_id, "dialogue", dialogue_text, actor_name=npc_name,
            )
            _attach_npc_portrait(db, session_id, module, ev)

    # 世界记忆钩子 c：续写里的 NPC 台词同样记入其互动史
    _record_npc_say_memory(
        db, session_id, game_session, module, cont_result[2],
        [player_char.name] + [t.name for t in (teammates or [])],
    )

    # 续写里可能含查完规则后发起的检定/场景切换等，照常处理（但禁止再次查阅）
    async for chunk in _process_commands(
        db, session_id, cont_result[1], module, player_char, game_session, llm,
        teammates=teammates, allow_rule_lookup=False, lookup_depth=lookup_depth + 1,
    ):
        yield chunk


async def _handle_module_lookup(
    db: Session,
    session_id: str,
    query: str,
    module: Module,
    player_char: Character,
    game_session: GameSession,
    llm,
    teammates: list[Character] | None = None,
    lookup_depth: int = 0,
) -> AsyncIterator[str]:
    """KP 发起 [MODULE_LOOKUP] 后：检索模组原文 → 回灌让 KP 据此续写。

    与 [RULE_LOOKUP] 同一套处理模式，且共享 lookup_depth 配额（合并计数）。
    透明提示一条 ephemeral system（不落库）；检索不到/失败时给降级文案让 KP
    按结构化模组资料续写（fail-open，不阻塞跑团）。
    """
    yield _make_chunk("system", "守秘人翻阅模组手稿……")

    passages = _module_lookup_passages(db, module, game_session, query)
    continuation = KP_MODULE_CONTINUATION_PROMPT.format(query=query, passages=passages)
    events = session_service.get_session_events(db, session_id)
    messages = build_kp_context(
        game_session, module, player_char, events, teammates=teammates,
        rules_lookup_enabled=False,  # 续写阶段不再广告查阅，避免长链
    )
    messages.append({"role": "user", "content": continuation})

    kp = KPAgent(llm)
    cont_result = ["", "", [], [], []]
    try:
        async for chunk in _stream_narration_filtered(
            kp, messages, cont_result, npcs=_matcher_npcs(module, teammates, game_session),
        ):
            yield chunk
    finally:
        cont_narration = cont_result[0].rstrip()
        if cont_narration:
            session_service.add_event(
                db, session_id, "narration", cont_narration, actor_name="KP",
            )
        for npc_name, dialogue_text in cont_result[2]:
            ev = session_service.add_event(
                db, session_id, "dialogue", dialogue_text, actor_name=npc_name,
            )
            _attach_npc_portrait(db, session_id, module, ev)

    # 续写里可能含查完原文后发起的检定/场景切换等，照常处理（但禁止再次查阅）
    async for chunk in _process_commands(
        db, session_id, cont_result[1], module, player_char, game_session, llm,
        teammates=teammates, allow_rule_lookup=False, lookup_depth=lookup_depth + 1,
    ):
        yield chunk


async def _handle_recall_history(
    db: Session,
    session_id: str,
    query: str,
    module: Module,
    player_char: Character,
    game_session: GameSession,
    llm,
    teammates: list[Character] | None = None,
    lookup_depth: int = 0,
) -> AsyncIterator[str]:
    """KP 发起 [RECALL_HISTORY] 后：回捞本局早前的事件原文 → 回灌让 KP 据此续写。

    与 [RULE_LOOKUP]/[MODULE_LOOKUP] 同一套处理模式并共享 lookup_depth 配额，区别在于
    查的是「本局已经发生过的事」：那部分记忆被滚动摘要压缩过，凭梗概编造出来的往事会
    与玩家的记录冲突。检索不到时给降级文案，让 KP 当作确实没发生过（fail-open）。
    """
    yield _make_chunk("system", "守秘人回想早前的事……")

    passages = _recall_history_passages(db, session_id, game_session, query)
    continuation = KP_RECALL_CONTINUATION_PROMPT.format(query=query, passages=passages)
    events = session_service.get_session_events(db, session_id)
    messages = build_kp_context(
        game_session, module, player_char, events, teammates=teammates,
        rules_lookup_enabled=False,  # 续写阶段不再广告查阅，避免长链
    )
    messages.append({"role": "user", "content": continuation})

    kp = KPAgent(llm)
    cont_result = ["", "", [], [], []]
    try:
        async for chunk in _stream_narration_filtered(
            kp, messages, cont_result, npcs=_matcher_npcs(module, teammates, game_session),
        ):
            yield chunk
    finally:
        cont_narration = cont_result[0].rstrip()
        if cont_narration:
            session_service.add_event(
                db, session_id, "narration", cont_narration, actor_name="KP",
            )
        for npc_name, dialogue_text in cont_result[2]:
            ev = session_service.add_event(
                db, session_id, "dialogue", dialogue_text, actor_name=npc_name,
            )
            _attach_npc_portrait(db, session_id, module, ev)

    async for chunk in _process_commands(
        db, session_id, cont_result[1], module, player_char, game_session, llm,
        teammates=teammates, allow_rule_lookup=False, lookup_depth=lookup_depth + 1,
    ):
        yield chunk
