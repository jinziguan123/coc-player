from __future__ import annotations

import asyncio
import logging
import re
import time

import httpx

from sqlalchemy.orm import Session

from app.ai import call_budget, turn_planner, usage_tracker
from app.ai import tools as kp_tools
from app.ai.agents.kp_agent import KPAgent
from app.ai.context import build_kp_context
from app.ai.llm_factory import get_fast_llm, get_llm
from app.ai.provider import StreamStalled, model_name
from app.services import style_editor as style_editor  # 兼容测试与插件的旧注入点
from app.ai.providers import openai_compat
from app.config import settings
from app.ai.prompts.kp_system import (
    CHECK_REQUEST_PROMPT,
    COMBAT_AFTERMATH_PROMPT,
    KP_DICE_CONTINUATION_PROMPT,
    KP_EPILOGUE_PROMPT,
)
from app.models.character import Character
from app.models.module import Module
from app.models.session import GameSession
from app.services import (
    agent_trace,
    character_chronicle,
    check_gating,
    effect_channel,
    chat_event_writer,
    command_protocol,
    dice_runtime,
    entity_state,
    event_protocol,
    event_recall,
    execution_scope,
    generation_housekeeping,
    generation_lifecycle,
    human_kp_actions,
    illustration_service,
    inventory_service,
    kp_actions,
    kp_tool_loop,
    movement,
    narration_protocol,
    npc_identity,
    npc_runtime,
    planned_effects,
    plot_progress,
    combat_service,
    rule_options_service,
    rulebook_service,
    session_service,
    team_turn_service,
    turn_context,
    turn_pipeline,
    turn_effects,
    turn_event_order,
    world_state,
)
from app.services.event_protocol import luck_offer_event_id
from app.services.room_hub import room_hub

from app.services.turn_pipeline import NarrationResult

logger = logging.getLogger(__name__)


def _npc_dialogue_guard(db, session_id, game_session, module, llm, player_char):
    """旧文本路径 / 分头路径的 NPC 守卫；无 LLM 的确定性测试环境不启用。"""
    if llm is None:
        return None
    runtime = npc_runtime.NpcRuntime(
        db, session_id, game_session, module, llm, player_char,
    )
    return npc_runtime.NpcDialogueGuard(runtime)


# 兼容既有调用与测试；协议的单一事实来源位于 command_protocol。
DICE_CHECK_RE = command_protocol.DICE_CHECK_RE
OPPOSED_CHECK_RE = command_protocol.OPPOSED_CHECK_RE
SAN_CHECK_RE = command_protocol.SAN_CHECK_RE
HP_CHANGE_RE = command_protocol.HP_CHANGE_RE
NPC_ACT_RE = command_protocol.NPC_ACT_RE
SCENE_CHANGE_RE = command_protocol.SCENE_CHANGE_RE
RULE_LOOKUP_RE = command_protocol.RULE_LOOKUP_RE
MODULE_LOOKUP_RE = command_protocol.MODULE_LOOKUP_RE
SET_FLAG_RE = command_protocol.SET_FLAG_RE
CLEAR_FLAG_RE = command_protocol.CLEAR_FLAG_RE
HANDOUT_RE = command_protocol.HANDOUT_RE
GROUP_RE = command_protocol.GROUP_RE
CMD_TAG_PREFIXES = command_protocol.CMD_TAG_PREFIXES
MAX_RULE_LOOKUPS = command_protocol.MAX_RULE_LOOKUPS
MAX_DICE_CONTINUATIONS = command_protocol.MAX_DICE_CONTINUATIONS
_is_cmd_tag = command_protocol.is_command_tag
_parse_tag_kv = command_protocol.parse_tag_kv
split_speech_action = event_protocol.split_speech_action
split_ooc = event_protocol.split_ooc
_make_chunk = event_protocol.make_chunk
event_to_chunk = event_protocol.event_to_chunk
_strip_speaker_prefix = narration_protocol._strip_speaker_prefix
_narr_quote_span = narration_protocol._narr_quote_span
_is_party_speaker = narration_protocol._is_party_speaker
_filter_narration_stream = narration_protocol.filter_narration_stream
_persist_error_notice = chat_event_writer.persist_error_notice
_extract_leaked_dialogue = chat_event_writer._extract_leaked_dialogue
_record_chunk_event = chat_event_writer.record_chunk_event
_resolve_scene_ref = turn_context._resolve_scene_ref
_scene_name = turn_context._scene_name
_current_turn_events = turn_context._current_turn_events
_shown_turn_dialogues = turn_context._shown_turn_dialogues
_shown_turn_context = turn_context._shown_turn_context
_settled_checks_context = turn_context._settled_checks_context
commit_pending_travel = turn_context.commit_pending_travel
_location_groups = turn_context._location_groups
_augment_plan_with_backstage = turn_context._augment_plan_with_backstage
_team_blind_message = turn_context._team_blind_message
_apply_world_memory = turn_context._apply_world_memory
_match_single_npc = turn_context._match_single_npc
_record_npc_say_memory = turn_context._record_npc_say_memory
_snap_offset = turn_context._snap_offset
_remap_marks_after_rewrite = turn_context._remap_marks_after_rewrite
_recent_seen_text = turn_context._recent_seen_text
_validate_and_patch_narration = turn_context._validate_and_patch_narration
_scene_title = turn_context._scene_title
_latest_player_input = turn_context._latest_player_input
_module_excerpts_for_context = turn_context._module_excerpts_for_context
_plan_involves_san = turn_context._plan_involves_san
_rule_keywords_from_events = turn_context._rule_keywords_from_events
_san_context = turn_context._san_context
_recent_player_text = turn_context._recent_player_text
_rule_query = turn_context._rule_query
_retrieve_rules = turn_context._retrieve_rules
_rule_excerpts_for_context = turn_context._rule_excerpts_for_context
_rule_excerpts_for_planner = turn_context._rule_excerpts_for_planner
_record_rag = turn_context._record_rag
_record_turn_usage = turn_context._record_turn_usage
DEFAULT_NPC_SKILL = dice_runtime.DEFAULT_NPC_SKILL
ALWAYS_BLIND_SKILLS = dice_runtime.ALWAYS_BLIND_SKILLS
DIFFICULTY_LABEL = dice_runtime.DIFFICULTY_LABEL
TIER_LABEL = dice_runtime.TIER_LABEL
_check_prompt_text = dice_runtime._check_prompt_text
_resolve_check_actor = dice_runtime._resolve_check_actor
_parse_bonus_penalty = dice_runtime._parse_bonus_penalty
_check_dice_detail = dice_runtime._check_dice_detail
_pool_dice_detail = dice_runtime._pool_dice_detail
_exec_generic_roll = dice_runtime._exec_generic_roll
_scene_requires_group_check = dice_runtime._scene_requires_group_check
_resolve_san_targets = dice_runtime._resolve_san_targets
_present_party = dice_runtime._present_party
_resolve_dice_group_targets = dice_runtime._resolve_dice_group_targets
_resolve_opposed = dice_runtime._resolve_opposed
_ALL_TOKENS = dice_runtime._ALL_TOKENS
# 测试与插件仍会在 chat_service 上替换校验器；保留模块级兼容引用。
turn_validator = turn_context.turn_validator
MAX_TEAMMATES_PER_TURN = team_turn_service.MAX_TEAMMATES_PER_TURN
TEAM_ACTION_EVENT = team_turn_service.TEAM_ACTION_EVENT
_matcher_npcs = team_turn_service._matcher_npcs
_stream_narration_filtered = team_turn_service._stream_narration_filtered
_parse_team_decision = team_turn_service._parse_team_decision
_run_team_turn = team_turn_service._run_team_turn
TeamAgent = team_turn_service.TeamAgent
_classify_llm_error = generation_lifecycle.classify_llm_error


def _timed(session_id: str, label: str, *, drain: bool = True):
    """本模块各条生成路径共用的计时包装（见 generation_lifecycle.generation_timing）。"""
    return generation_lifecycle.generation_timing(
        session_id, label, drain=_drain_housekeeping if drain else None,
    )
_housekeeping_manager = generation_lifecycle.HousekeepingManager()
_housekeeping_tasks = _housekeeping_manager.tasks
STORY_SUMMARY_TRIGGER_RATIO = generation_housekeeping.STORY_SUMMARY_TRIGGER_RATIO
STORY_SUMMARY_KEEP_RATIO = generation_housekeeping.STORY_SUMMARY_KEEP_RATIO
STORY_SUMMARY_MIN_KEEP = generation_housekeeping.STORY_SUMMARY_MIN_KEEP
BACKSTAGE_TURN_INTERVAL = generation_housekeeping.BACKSTAGE_TURN_INTERVAL
BACKSTAGE_DO_NOT_REVEAL_MAX = generation_housekeeping.BACKSTAGE_DO_NOT_REVEAL_MAX
_maybe_roll_story_summary = generation_housekeeping._maybe_roll_story_summary
_maybe_run_backstage = generation_housekeeping._maybe_run_backstage
story_summarizer = generation_housekeeping.story_summarizer
backstage_agent = generation_housekeeping.backstage_agent
_illustrate_event = illustration_service._illustrate_event
_illustrate_handout = illustration_service._illustrate_handout
_spawn_illustration = illustration_service._spawn_illustration
_module_list_cache_writer = illustration_service._module_list_cache_writer
_scene_variant_cache_writer = illustration_service._scene_variant_cache_writer
_module_era = illustration_service._module_era
_scene_visual_state = illustration_service._scene_visual_state
_scene_card_key = illustration_service._scene_card_key
_maybe_scene_illustration = illustration_service._maybe_scene_illustration
_maybe_clue_illustration = illustration_service._maybe_clue_illustration
_maybe_encounter_illustration = illustration_service._maybe_encounter_illustration
_attach_npc_portrait = illustration_service._attach_npc_portrait
_attach_npc_portraits = illustration_service._attach_npc_portraits
_PORTRAIT_INFLIGHT = illustration_service._PORTRAIT_INFLIGHT


async def _drain_housekeeping(session_id: str) -> None:
    """等待上一轮后台收尾，避免 world_state 并发读改写。"""
    await _housekeeping_manager.drain(session_id)


def _spawn_housekeeping(session_id: str) -> None:
    """启动独立数据库会话中的摘要与幕后推演。

    **走快模型**：滚动摘要与幕后推演都是结构化副任务（浓缩既往事件、按 NPC 动机推演），
    不吃文笔，正是「快模型」这档的既定职责。此前误传主模型：主模型往往开着思考换文笔，
    实测收尾因此要 30.9s。

    而这 30.9s 不只是后台慢——**下一回合开头与投骰后的 KP 续写都要等它 drain 完**才能动
    world_state，于是它直接变成玩家的等待。
    """
    _housekeeping_manager.spawn(
        session_id,
        get_fast_llm(),
        _maybe_roll_story_summary,
        _maybe_run_backstage,
    )


async def _finish_generation(db: Session, session_id: str, llm) -> None:
    """先广播完成，再异步启动收尾（收尾自取快模型，与本次叙事用的 llm 无关）。"""
    room_hub.broadcast(session_id, _make_chunk("done"))
    _spawn_housekeeping(session_id)


def _persist_narration(
    db: Session, session_id: str, result: list, event_order: list | None = None,
    *, llm=None,
) -> None:
    """兼容入口；叙事清洗和落库由 chat_event_writer 负责。

    ``llm`` 是生成这段旁白的 Provider（或包着它的 KPAgent），只用来取模型名打进 metadata。
    """
    chat_event_writer.persist_narration(
        db,
        session_id,
        result,
        event_order,
        attach_npc_portraits=_attach_npc_portraits,
        model=model_name(llm),
    )


def _settle_plan_ending(
    db: Session,
    session_id: str,
    game_session: GameSession,
    module: Module | None,
    plan: turn_planner.TurnPlan | None,
) -> None:
    """规划器判定本轮抵达模组结局 → 落 world_state.ending_reached + 广播一条系统提示。

    id 必须命中模组 endings（编出来的一律忽略），并把结局名/收场回填进 plan.ending——
    KP 的裁定计划里据此多出一段「本轮当终局演」的指令，不然「抵达终局」就只是系统自己记了
    一笔，KP 那边照旧当普通一轮往下讲。

    **只记录与提示，绝不代替玩家结束模组**：结束仍须全体真人投票（/end-vote）。幂等：
    已抵达过就不再重复（此后的回合仍可继续演尾声）。
    """
    verdict = getattr(plan, "ending", None)
    eid = str(getattr(verdict, "reached_id", "") or "").strip()
    if not eid or (game_session.world_state or {}).get("ending_reached"):
        return
    endings = getattr(module, "endings", None) or []
    match = next(
        (e for e in endings if isinstance(e, dict) and str(e.get("id") or "") == eid), None,
    )
    if match is None:
        logger.info("规划器给了模组里没有的结局 id=%s，忽略 session=%s", eid, session_id)
        verdict.reached_id = ""
        return
    name = str(match.get("name") or eid).strip()
    verdict.name = name
    verdict.description = str(match.get("description") or "").strip()
    world_state.set_key(db, game_session, "ending_reached", {
        "id": eid, "name": name, "reason": str(verdict.reason or "").strip(),
    })
    content = f"（本模组已抵达结局：{name}。演完这一幕后，全体玩家一致同意即可结束本局。）"
    ev = session_service.add_event(
        db, session_id, "system", content, actor_name="系统",
        metadata={"ending_reached": True, "ending_id": eid, "ending_name": name},
    )
    room_hub.broadcast(session_id, _make_chunk(
        "system", content, actor_name="系统", event_id=ev.id,
        metadata={"ending_reached": True, "ending_id": eid, "ending_name": name},
    ))
    room_hub.broadcast(session_id, _make_chunk("status", metadata={"ending_reached": True}))


def _record_clue_ledger_from_plan(
    db: Session,
    game_session: GameSession,
    plan: turn_planner.TurnPlan,
    events: list,
    player_char: Character,
    teammates: list[Character] | None,
    module: Module | None = None,
) -> None:
    """兼容入口；世界记忆更新由 turn_context 负责，首次线索配图通过端口触发。"""
    turn_context.record_clue_ledger_from_plan(
        db,
        game_session,
        plan,
        events,
        player_char,
        teammates,
        module,
        on_first_clue=_maybe_clue_illustration,
    )

# DICE_CHECK 升级为键值解析（参数顺序无关）：skill=必填；difficulty/char/chars/visibility 选填。
# char=对谁投（空/主角=主角，队友名，NPC 名）；visibility=open|blind（blind=暗投/暗骰，结果只给 KP）。
# KP 有时（尤其多人回合）不发 [DICE_CHECK]、而是把「X 检定（normal）：困难成功 (10 ≤ 60)」这类
# **机检结果行**当散文写进旁白——那本是系统掷骰后才产生的内容，KP 自撰＝伪造结果，且玩家看不到
# 投骰提示/动画、结果卡也渲染不出。落库前确定性剥除这类行（要求「检定（<真实难度词>）：<成败等级>」
# 连写，机检签名极强、正常叙事不会出现，误伤概率极低）。配套 kp_system 规则3 的提示词硬约束。
# 对抗骰：两方各投同名或不同技能，比成功等级。a/b 为角色名（主角/队友/NPC）。
# SAN_CHECK 升级为键值解析：success_loss/failure_loss + chars=（目睹者，缺省在场全体）
# + source=（恐怖源标识，用于「同一角色对同一恐怖只检定一次」的去重）。各角色各自结算。
# 模组原文查阅：与 RULE_LOOKUP 同一套终止性指令模式，共享每轮查阅配额。
# 剧情状态推进：KP 在叙事节拍发 [SET_FLAG: flag=xxx] 置标志、[CLEAR_FLAG: flag=xxx] 清标志，
# 场景/NPC 的状态变体据此切换（如「地下室进水后变致命」「危险消退」）。是内部控制标签，不展示给玩家。
# 容忍：漏写「flag=」、冒号写成空格（如「[SET_FLAG hint_x]」）。全角括号在处理前已归一为半角。
# 手书发放：KP 在剧情达成发放条件时发 [HANDOUT: id=xxx]，系统把该手书原文以信笺卡片发给全桌。
# 容忍漏写「id=」、冒号写成空格（与 SET_FLAG 同款宽容）。全角括号在处理前已归一为半角。
# 分头行动：KP 在每个分组/场景内容前标 [GROUP: scene=<场景标签>]，后续内容归该组，前端据此分栏。内联剔除。
# 注：场景瓦片地图已下线，[MOVE]/[MAP_MARK] 不再广告也不再执行；流过滤器仍静默吞掉这两个
# 标签的残余文本形态（见 _stream_narration_filtered 的 startswith 分支），防止泄给玩家。















def _reorder_turn_events(
    db: Session, session_id: str, event_order: list, base_seq: int
) -> None:
    """兼容旧调用点；实际实现位于独立的回合事件顺序服务。"""
    turn_event_order.reorder_turn_events(db, session_id, event_order, base_seq)




SPLIT_FOCUS_PROMPT = (
    "本回合队伍分头行动。现在【只】叙述「{label}」这个场景里发生的事：描写此地的环境、气氛，"
    "以及在场 NPC 对 {members} 言行的反应与由此推进的后续。\n"
    "要求：①详尽完整，与其他分组同等篇幅；②只写这一场景，绝不叙述或提及其他分组的人"
    "（他们另行单独叙述）；③{members} 都是**玩家角色**——**绝不替他们说话、行动、做决定或描写其"
    "心理感受**，只呈现世界与 NPC 对其已有言行的回应；"
    "④**此地的 NPC 对其他分组在别处的言行一无所知**（除非大到隔墙可闻的巨响、或有人当面告知）——"
    "绝不让 NPC 评论、追问或以任何方式反应它感知之外的事。"
)








# 兼容既有调用；规划副作用的单一实现位于 planned_effects。
_ensure_planned_combat = planned_effects._ensure_planned_combat
_ensure_narrated_combat = planned_effects._ensure_narrated_combat
_san_rolled_this_turn = planned_effects._san_rolled_this_turn
_ensure_planned_sanity = planned_effects._ensure_planned_sanity
_hp_changed_this_turn = planned_effects._hp_changed_this_turn
_ensure_planned_mishap = planned_effects._ensure_planned_mishap
_ensure_planned_items = planned_effects._ensure_planned_items
_ensure_planned_combat_damage = planned_effects._ensure_planned_combat_damage
_ensure_planned_scene = planned_effects._ensure_planned_scene
_ensure_scene_entry_checks = planned_effects._ensure_scene_entry_checks




def _validator_note(session_id: str):
    """校验器真要调 LLM 时，告诉玩家这段静默在做什么（叙事流此时已经停了）。"""
    return lambda: room_hub.broadcast(
        session_id, _make_chunk("housekeeping", "守秘人正在复核这段叙述…"),
    )


async def _run_narration_stage(
    db, session_id, game_session, module, player_char, teammates, llm,
    stream, result: NarrationResult, *, plan=None, events=None,
    event_order=None, base_seq=0, party_names=None, turn_inputs="",
    location_guard="", listeners=None, group=None, hold=False, after_generate=None,
):
    """所有入口共用校验、持久化、重排和 NPC 记忆处理；兼容注入点在编排层保留。"""
    order = event_order if event_order is not None else []
    # 高风险回合（本轮有明确不可泄露的内容）：整段缓冲到终检完成再放出，牺牲首字延迟换取可拦截。
    high_risk = bool(
        settings.narration_buffer_high_risk and plan is not None and plan.safety.do_not_reveal
    )
    ctx = turn_pipeline.TurnContext(
        session_id, result, turn_pipeline.TurnSpec(
            "KP 叙事", hold_for_check=hold, buffer_until_validated=high_risk,
            validate_timeout=settings.narration_buffer_timeout_seconds if high_risk else None,
            on_validation_failure=settings.narration_buffer_on_failure,
        ),
        pre_gen_seq=base_seq, event_order=order, group=group,
    )
    if high_risk:
        room_hub.broadcast(session_id, _make_chunk("housekeeping", "守秘人正在斟酌措辞…"))

    async def validate():
        extra = {"report": ctx.validation} if high_risk else {}
        await _validate_and_patch_narration(
            llm, plan, result, event_order,
            seen_context=_recent_seen_text(events or []), turn_inputs=turn_inputs,
            on_start=_validator_note(session_id),
            settled_checks=_settled_checks_context(events or []),
            party_names=party_names, location_context=location_guard, **extra,
        )

    def persist():
        _persist_narration(db, session_id, result, event_order, llm=llm)
        if event_order is not None:
            _reorder_turn_events(db, session_id, event_order, base_seq)

    await turn_pipeline.run(
        ctx, stream, broadcast=lambda chunk: room_hub.broadcast(session_id, chunk),
        validate=validate, persist=persist, after_generate=after_generate,
        keep=_check_request_narration_keep,
    )
    _record_npc_say_memory(
        db, session_id, game_session, module, result.bubbles,
        listeners if listeners is not None else [player_char.name] + [t.name for t in (teammates or [])],
    )


async def _run_generation(
    db: Session,
    session_id: str,
    game_session: GameSession,
    module: Module,
    player_char: Character,
    events: list,
    teammates: list[Character] | None = None,
    blind_results: list[str] | None = None,
    plan: turn_planner.TurnPlan | None = None,
    finish: bool = True,
) -> None:
    # 不支持原生工具调用时经文本指令适配层，同样走工具循环（见 kp_tool_loop.loop_llm）
    llm = kp_tool_loop.loop_llm(get_llm())
    # 仅在非开场、且该规则系统已挂载规则书时，向 KP 广告 [RULE_LOOKUP] 能力
    rules_enabled = bool(events) and rulebook_service.has_rulebook(db, module.rule_system)
    # 仅在非开场、且模组原文索引就绪时，向 KP 广告 [MODULE_LOOKUP] 能力（镜像规则书模式）
    module_rag_enabled = bool(events) and getattr(module, "rag_status", "") == "ready"
    party_ids = {player_char.id} | {t.id for t in (teammates or [])}
    shown_dialogues = _shown_turn_dialogues(events, party_ids)
    turn_inputs = _shown_turn_context(events, party_ids)
    matcher_npcs = _matcher_npcs(module, teammates, game_session)
    # 生成前基线序号：供确定性 SAN 守卫判断「本轮 KP 是否已自行掷过 SAN」（幂等，防重复扣）。
    pre_gen_seq = session_service.get_next_sequence_num(db, session_id) - 1
    combat_before = bool(combat_service.get_combat(game_session))

    # 回合裁定计划：主链路（run_chat_generation）已在队友回合之前先跑好 plan 并记过线索台账，
    # 通过 plan 参数传入 → 此处不重复调用。其他入口（run_travel_generation / _run_kp_turn 尾部）
    # 不传 plan → 这里现跑并记账（钩子 a），行为与前移前完全一致。开场（无事件）不跑。
    if plan is None and events:
        # 调用方没带 plan（重新生成等路径）时在这里现跑一次 planner：也要打点，
        # 否则这些路径的耗时只有一个合计、拆不出 planner 占了多少。
        t_plan = time.monotonic(); u_plan = usage_tracker.snapshot()
        plan_messages = turn_planner.build_turn_plan_messages(
            game_session, module, player_char, events, teammates=teammates,
            rules_lookup_enabled=rules_enabled,
            rule_excerpts=_rule_excerpts_for_planner(db, module, events, game_session),
            rule_options_block=rule_options_service.context_block(db, game_session),
        )
        with agent_trace.span("planner", "规划本回合", session_id=session_id, db=db):
            plan = await turn_planner.run_turn_planner(get_fast_llm(), plan_messages)
        logger.info(
            "耗时|planner（生成内） %.1fs（%s）session=%s",
            time.monotonic() - t_plan, usage_tracker.fmt(usage_tracker.delta(u_plan)), session_id,
        )
        planned_effects.enforce_plan_item_locations(
            plan, module, game_session.current_scene_id,
        )
        plot_progress.sanitize_plan_flags(plan, module, entity_state.active_flags(game_session))
        # 世界记忆钩子 a：本轮裁定要揭示线索 → 写入线索台账（纯确定性，零额外 LLM 调用）
        if plan is not None:
            _record_clue_ledger_from_plan(
                db, game_session, plan, events, player_char, teammates, module=module,
            )
            _settle_plan_ending(db, session_id, game_session, module, plan)

    # 幕后推演 → validator 预筛：最近幕后事件文本挂进 plan.safety.do_not_reveal，
    # 防 KP 把「玩家不可见」的幕后动态直接复述进旁白（单场景与分头路径共用此 plan）。
    if plan is not None:
        _augment_plan_with_backstage(plan, events)

    # 移动裁定（叙事前）：规划器的结构化移动提案经原话与连通证据复核，可执行的此刻就落位——
    # 下面的分头分组、NPC 在场与 KP 上下文都以新位置为准，不会叙事还在旧场景、最后才换地图。
    # 需检定的暂存到骰子落地；商量/条件句/走近/去向不明/受阻只给 KP 一句裁定说明，不动人。
    move_note = ""
    if plan is not None and events:
        move_chunks, move_note = movement.adjudicate_plan(
            db, session_id, game_session, module, player_char, teammates, plan,
            pre_gen_seq=pre_gen_seq,
        )
        for chunk in move_chunks:
            room_hub.broadcast(session_id, chunk)
        db.refresh(game_session)

    # 位置意图硬闸：玩家只是提到/打算去别处、没有移动证据时，先清掉 plan 里的 scene_change，
    # 并生成一段注入 KP 上下文的位置硬约束。必须在生成前执行——生成后再拦截只能保住地图，
    # 保不住已经广播/落库的旁白。
    location_guard = ""
    if plan is not None and events:
        location_guard = _location_intent_guard(
            db, session_id, game_session, module, player_char, events, plan,
        )

    # 分头行动：按各成员「真实所在场景」归并（玩家经大地图、队友经 travel 动作更新的确定性位置）。
    # 身处 ≥2 个场景即分头 → 逐场景生成叙事。不再靠 LLM 猜分组、也不因「打算去X」误判。
    scene_groups = _location_groups(game_session, module, player_char, teammates)

    # 本回合队友暗骰（心理学等）的真实结果 → 一条「仅 KP 可见」的上下文消息（不落库/不广播）。
    blind_message = _team_blind_message(blind_results)

    # 规则书要点被动注入：按本轮 plan.turn_kind 预取规则条文（与 [RULE_LOOKUP] 主动查互补）。
    # 规则片段不依赖场景，分头行动时各分组共用同一份检索结果（与模组摘录「分头也注入」对齐）。
    rule_excerpts = _rule_excerpts_for_context(db, module, plan, events, game_session)

    if len(scene_groups) >= 2:
        # 分头行动：每组各跑一次工具循环（见 _run_split_generation）。
        await _run_split_generation(
            db, session_id, game_session, module, player_char, events,
            teammates, llm, rules_enabled, matcher_npcs, scene_groups,
            plan=plan, blind_message=blind_message, rule_excerpts=rule_excerpts,
            location_guard=location_guard, move_note=move_note, finish=finish,
            combat_before=combat_before,
        )
        return

    messages = build_kp_context(
        game_session, module, player_char, events, teammates=teammates,
        rules_lookup_enabled=rules_enabled,
        module_excerpts=_module_excerpts_for_context(
            db, module, game_session, events, party_ids,
        ),
        module_lookup_enabled=module_rag_enabled,
        rule_excerpts=rule_excerpts,
        recall_enabled=event_recall.is_enabled(game_session),
        # 走到这里就是「系统判定全队在一起」（≥2 组已在上面分流）。把这个结论明写给 KP，
        # 否则它只看得到一个全局当前场景，会拿历史里的「我们分头吧」当已经分头了。
        scene_groups=scene_groups,
        rule_options_block=rule_options_service.context_block(db, game_session),
    )
    # 战斗结果摘要已注入本轮上下文 → 清除，避免下一轮重复注入（读一次）。
    if (game_session.world_state or {}).get("combat_result"):
        ws = dict(game_session.world_state)
        ws.pop("combat_result", None)
        game_session.world_state = ws
        db.commit()
    if plan is not None:
        messages.append(turn_planner.build_turn_plan_message(plan))
    if blind_message is not None:
        messages.append(blind_message)
    if location_guard:
        messages.append({"role": "system", "content": location_guard})
    if move_note:
        messages.append({"role": "system", "content": move_note.strip()})

    # 玩家党名单（玩家 + AI 队友）：供台词归属守卫用——KP 绝不能用气泡替他们说话。
    party_names = {player_char.name} | {t.name for t in (teammates or [])}
    result = NarrationResult()
    # agent loop：指令在 loop 内经执行器完成（原生工具调用，或经适配层转换的文本指令）。
    # 本局启用的规则组件（含插件）声明的 KP 工具：同时进工具模式说明、原生 schema 与执行器。
    rs = rule_options_service.ruleset(db, game_session)
    messages.extend(kp_tool_loop.tool_mode_messages(llm, rs.kp_tools))
    exclude = set()
    if not rules_enabled:
        exclude.add("rule_lookup")   # 未挂规则书：不提供该工具
    if not module_rag_enabled:
        exclude.add("module_lookup")  # 原文索引未就绪：同上
    execute = _build_kp_tool_executor(
        db, session_id, game_session, module, player_char, teammates, llm, result,
        ruleset=rs, pre_gen_seq=pre_gen_seq,
    )
    # 本轮基线序号 + 事件广播顺序清单：loop 内工具事件即时落库（较小序号），旁白收尾才落库，
    # 直接 resync 会顺序错乱；收尾按广播偏移把本轮（seq>base_seq）事件重排回交错顺序。
    base_seq = session_service.get_next_sequence_num(db, session_id) - 1
    event_order: list = []
    tools = kp_tools.openai_tool_schemas(exclude=exclude, extra=rs.kp_tools)
    usage_probe: dict = {}
    stream = _run_kp_agent_loop(
        llm, messages, result, execute, tools=tools,
        npcs=matcher_npcs, plan=plan, party_names=party_names,
        shown_dialogues=shown_dialogues, event_order=event_order,
        masker_factory=lambda: npc_identity.build_masker(db, session_id, module),
        usage_probe=usage_probe,
        npc_guard=_npc_dialogue_guard(db, session_id, game_session, module, llm, player_char),
    )
    await _run_narration_stage(
        db, session_id, game_session, module, player_char, teammates, llm, stream, result,
        plan=plan, events=events, event_order=event_order, base_seq=base_seq,
        party_names=party_names, turn_inputs=turn_inputs,
        location_guard=location_guard + move_note,
        after_generate=lambda: _record_turn_usage(
            db, game_session, llm, events, messages,
            tools=tools, calibration_usage=usage_probe.get("first"),
        ),
    )

    await _run_post_effects(
        db, session_id, game_session, module, player_char, teammates, llm,
        allowed=turn_pipeline.TurnSpec.for_entry("regular").effects, plan=plan,
        pre_gen_seq=pre_gen_seq, combat_before=combat_before,
    )

    if finish:
        await _finish_generation(db, session_id, llm)


async def _run_pre_effects(
    db, session_id, game_session, player_char, teammates, *,
    allowed: frozenset[str], plan, requested_skill: str = "", module=None,
):
    """叙事前效果（``turn_pipeline.PRE_EFFECT_ORDER``）：在 KP 动笔前就该定下的确定性效果。"""
    if plan is None:
        return
    if "gated_items" in allowed:
        # 检定还没掷：收获一律按「需检定」暂存，骰子落地（手动或自动）再兑现。
        gated = plan.model_copy(update={
            "requires_check": True, "auto_outcome": "none",
            "check": plan.check.model_copy(update={
                "skill": plan.check.skill or requested_skill,
            }),
        })
        async for chunk in _ensure_planned_items(
            db, session_id, game_session, player_char, teammates, gated,
        ):
            room_hub.broadcast(session_id, chunk)
    if "moves" in allowed and module is not None:
        chunks, _note = movement.adjudicate_plan(
            db, session_id, game_session, module, player_char, teammates, plan,
        )
        for chunk in chunks:
            room_hub.broadcast(session_id, chunk)


async def _run_post_effects(
    db, session_id, game_session, module, player_char, teammates, llm, *,
    allowed: frozenset[str], plan, pre_gen_seq: int, combat_before: bool,
    plan_factory=None, requested_check=None,
):
    """所有入口共用的后置效果：按 ``turn_pipeline.POST_EFFECT_ORDER`` 的固定顺序执行入口允许的子集。

    ``plan_factory``（旁路续写）：回合起点没有计划，在第一个依赖计划的效果前现跑一次规划器——
    此时上下文已含本轮叙事与前面守卫落下的事件（补开战、进场检定）。
    每个守卫自身仍幂等；这里只负责「谁能跑、按什么顺序、回合挂起时跳过什么」。
    """
    def emit(chunk):
        room_hub.broadcast(session_id, chunk)

    async def drain(gen):
        async for chunk in gen:
            emit(chunk)

    if "planned_combat" in allowed:
        await drain(_ensure_planned_combat(
            db, session_id, game_session, module, player_char, teammates, llm, plan,
        ))
    # 叙事—机制脱节守卫：计划判了不开战（或旁路根本没有计划），KP 却把怪物写成已经扑上来 → 补开战。
    if "narrated_combat" in allowed:
        await drain(_ensure_narrated_combat(
            db, session_id, game_session, module, player_char, teammates, llm, plan, pre_gen_seq,
        ))

    # 玩家亲口申请的检定必须有归宿：KP 把它写成叙事顺过去时确定性补挂（详见守卫的文档）。
    # 放在挂起判定之前：补挂的检定同样让场景/进场检定/SAN 守卫让路。
    if "requested_check" in allowed and requested_check is not None:
        await drain(requested_check())

    # 回合挂起感知：KP 已挂出待投检定（结果未定）或本轮刚开战时，守卫不再替未定的结果
    # 搬场景、补进场检定或补发被检定门控的 SAN——这些留给投骰后的续写或下一轮。
    hung_check = turn_context.checks_hung_since(db, session_id, pre_gen_seq)
    db.refresh(game_session)
    combat_started = not combat_before and bool(combat_service.get_combat(game_session))
    if hung_check or combat_started:
        logger.info(
            "回合已挂起（待投检定=%s 新开战=%s），跳过部分后置守卫: session=%s",
            hung_check, combat_started, session_id,
        )

    def gated() -> bool:
        return hung_check and bool(plan and plan.requires_check)

    # 确定性场景守卫先执行：真实进入目标场景后，后续 SAN 守卫才能读取该场景的模组机制。
    if "scene" in allowed and not (gated() or combat_started):
        await drain(_ensure_planned_scene(
            db, session_id, game_session, module, player_char, teammates, plan,
            pre_gen_seq=pre_gen_seq,
        ))
    # 确定性进场检定守卫：模组写明「进入本场景时投X」的机制点 → 后端补发（每角色一次）。
    # 已有待投检定或刚开战时不叠卡；按角色幂等，下一轮仍会补上。
    if "entry_checks" in allowed and not (hung_check or combat_started):
        await drain(_ensure_scene_entry_checks(
            db, session_id, game_session, module, player_char, teammates,
        ))

    if plan is None and plan_factory is not None:
        plan = await plan_factory()

    # 确定性 SAN 守卫：计划裁定本轮目睹恐怖但 KP 漏发 SAN → 后端补发（幂等）。
    # 计划本身要求检定且该检定仍待投时，恐怖多半由检定揭示，交给投骰后续写的 SAN 守卫。
    if "sanity" in allowed and not gated():
        await drain(_ensure_planned_sanity(
            db, session_id, game_session, player_char, teammates, plan, pre_gen_seq,
            module=module,
        ))
    if "mishap" in allowed:
        await drain(_ensure_planned_mishap(
            db, session_id, player_char, teammates, plan, pre_gen_seq, module=module,
        ))
    # 确定性库存守卫：计划裁定的物品获得/失去 → 后端确定性增减（幂等），库存是权威状态。
    if "items" in allowed:
        await drain(_ensure_planned_items(
            db, session_id, game_session, player_char, teammates, plan,
        ))
    # 确定性战斗伤害守卫：战斗中非常规/范围攻击 → 挂成玩家 pending_roll 亲手掷、扣敌人 HP。
    if "combat_damage" in allowed:
        await drain(_ensure_planned_combat_damage(db, session_id, player_char, plan))
    # 剧情进度外置：规划器裁定本轮满足的剧情钩子 → 校验后确定性落标志（+ KP 专属事件）。
    if "flags" in allowed:
        await drain(plot_progress.apply_planned_flags(db, session_id, game_session, module, plan))
    # 叙事进度记账（放在全部守卫之后：场景守卫可能刚把队伍挪进新场景）：本轮演过的场景
    # 机制点 / 摆到玩家面前的线索 → 确定性写世界记忆，补规划器漏记的那些（详见守卫文档）。
    if "narrated_progress" in allowed:
        planned_effects.record_narrated_progress(
            db, session_id, game_session, module, player_char, teammates, pre_gen_seq,
        )


def _tag_turn_events_by_group(db: Session, turn_events: list, groups: list[dict]) -> None:
    """把本回合各角色的事件按其所在分组补打 group 标签（玩家行动随其场景列同列）。

    掷骰事件（actor=系统）按其内容里的领头角色名（「亨利·卡特｜…」）归组。
    """
    name_to_group: dict[str, str] = {}
    for g in groups:
        for m in g["members"]:
            name_to_group[m] = g["label"]

    def _match(name: str) -> str | None:
        name = (name or "").strip()
        if not name:
            return None
        if name in name_to_group:
            return name_to_group[name]
        for full, label in name_to_group.items():
            if name in full or full in name:
                return label
        return None

    for e in turn_events:
        etype = getattr(e, "event_type", None)
        if etype not in ("action", "dialogue", "dice"):
            continue
        label = _match(e.actor_name or "")
        if not label and etype == "dice":
            head = re.split(r"[｜|]", e.content or "", 1)[0]
            label = _match(head)
        if label:
            session_service.set_event_group(db, e, label)


async def _apply_coordinator_intents(
    db, session_id, game_session, module, player_char, teammates, llm, intents,
) -> None:
    """统一执行分头组提交的全局工具；同类只接受第一条，其余明确拒绝。"""
    if not intents:
        return
    seen: set[tuple[str, str]] = set()
    for intent in intents:
        key = (
            intent.kind,
            str(intent.args.get("scene_id") or intent.args.get("enemies") or intent.args.get("pursuer") or ""),
        )
        if key in seen:
            room_hub.broadcast(session_id, _make_chunk(
                "system",
                f"本回合已有一个 {intent.kind} 请求被协调器接受，重复请求已忽略。",
            ))
            continue
        seen.add(key)
        if intent.kind == "scene_change":
            # 与组内工具同一入口：按本组原话推定移动者、按人过移动裁定；本组没人宣告、
            # 又不含主角时拒绝（分头组不能替主队做主）。
            chunks, _sid, note = await turn_effects.exec_tool_scene_change(
                db, session_id, game_session, module, intent.args.get("scene_id", ""),
                intent.args.get("movers", ""), player_char, teammates,
                scope_ids=set(intent.scope.actor_ids),
            )
            for chunk in chunks:
                room_hub.broadcast(session_id, chunk)
            if note:
                logger.info("协调器场景切换未执行：%s", note)
        elif intent.kind == "start_combat":
            chunks = await _exec_start_combat(
                db, session_id, game_session, module, player_char, teammates, llm,
                intent.args.get("enemies", ""), intent.args.get("trigger", ""),
                participant_ids=list(intent.scope.actor_ids),
            )
            for chunk in chunks:
                room_hub.broadcast(session_id, chunk)
        elif intent.kind == "start_chase":
            if not intent.scope.contains(player_char.id):
                room_hub.broadcast(session_id, _make_chunk(
                    "system", "分头组不能直接发起追逐；追逐请求已由协调器拒绝。",
                ))
                continue
            chunks = _exec_start_chase(
                db, session_id, module, player_char,
                intent.args.get("pursuer", ""), intent.args.get("trigger", ""),
            )
            for chunk in chunks:
                room_hub.broadcast(session_id, chunk)


async def _run_split_narrations_tool_loop(
    db: Session,
    session_id: str,
    game_session: GameSession,
    module: Module,
    player_char: Character,
    events: list,
    teammates: list[Character] | None,
    llm,
    rules_enabled: bool,
    matcher_npcs: list[dict],
    groups: list[dict],
    plan: turn_planner.TurnPlan | None = None,
    blind_message: dict | None = None,
    rule_excerpts: list[dict] | None = None,
    location_guard: str = "",
    group_prompts: dict[str, str] | None = None,
    default_prompt: str | None = None,
    move_note: str = "",
) -> None:
    """分头行动的工具循环版：每组一套聚焦上下文，工具即时执行并广播。

    要点：
    - 组内骰子候选域由 dice_scope 限定，不会把别组的人拉进群检；
    - 组内 NPC/场景工具直接作用于会话状态；
    - 叙事、检定、场景切换都在各组的 loop 内完成，不再把各组文本拼回去二次处理。
    """
    _tag_turn_events_by_group(db, _current_turn_events(events), groups)
    plan_message = turn_planner.build_turn_plan_message(plan) if plan is not None else None
    module_rag_enabled = bool(events) and getattr(module, "rag_status", "") == "ready"
    party_ids = {player_char.id} | {t.id for t in (teammates or [])}
    shown_dialogues = _shown_turn_dialogues(events, party_ids)
    turn_inputs = _shown_turn_context(events, party_ids)
    party_names = {player_char.name} | {t.name for t in (teammates or [])}
    rs = rule_options_service.ruleset(db, game_session)
    exclude: set[str] = set()
    if not rules_enabled:
        exclude.add("rule_lookup")
    if not module_rag_enabled:
        exclude.add("module_lookup")
    tools = kp_tools.openai_tool_schemas(exclude=exclude, extra=rs.kp_tools)
    pre_gen_seq = session_service.get_next_sequence_num(db, session_id) - 1
    by_name = {player_char.name: player_char}
    by_name.update({t.name: t for t in (teammates or [])})
    coordinator_intents: list = []

    for grp in groups:
        label = grp["label"]
        members = "、".join(grp["members"])
        char_scope = [by_name[n] for n in (grp.get("members") or []) if n in by_name]
        scope = execution_scope.ExecutionScope(
            group_id=label,
            scene_id=str(grp.get("scene_id") or game_session.current_scene_id or ""),
            actor_ids=tuple(c.id for c in char_scope),
            is_primary_group=player_char.id in {c.id for c in char_scope},
            split_mode=True,
        )
        messages = build_kp_context(
            game_session, module, player_char, events, teammates=teammates,
            rules_lookup_enabled=rules_enabled, viewer_scene_id=grp.get("scene_id"),
            module_excerpts=_module_excerpts_for_context(
                db, module, game_session, events, party_ids,
                scene_id=grp.get("scene_id"),
            ),
            module_lookup_enabled=module_rag_enabled,
            rule_excerpts=rule_excerpts,
            recall_enabled=event_recall.is_enabled(game_session),
            scene_groups=groups,
            rule_options_block=rule_options_service.context_block(db, game_session),
        )
        if plan_message is not None:
            messages.append(plan_message)
        if blind_message is not None:
            messages.append(blind_message)
        if location_guard and player_char.name in (grp.get("members") or []):
            messages.append({"role": "system", "content": location_guard})
        if move_note:
            # 移动裁定关乎每一组：走的人在新空间，留下的人在原处，各组都要按同一份位置叙述。
            messages.append({"role": "system", "content": move_note.strip()})
        # 与单场景工具循环一致：原生工具调用要告知模型用工具代替方括号指令（此前分头路径漏了这段）
        messages.extend(kp_tool_loop.tool_mode_messages(llm, rs.kp_tools))
        if group_prompts and label in group_prompts:
            user_content = group_prompts[label]
        else:
            prompt = default_prompt or SPLIT_FOCUS_PROMPT
            user_content = prompt.format(label=label, members=members)
        messages.append({"role": "user", "content": user_content})

        result = NarrationResult()
        base_seq = session_service.get_next_sequence_num(db, session_id) - 1
        event_order: list = []
        execute = _build_kp_tool_executor(
            db, session_id, game_session, module, player_char, teammates, llm, result,
            pre_gen_seq=pre_gen_seq, ruleset=rs, dice_scope=char_scope,
            scope=scope, coordinator_intents=coordinator_intents,
        )
        stream = _run_kp_agent_loop(
            llm, messages, result, execute,
            tools=tools, npcs=matcher_npcs, group_label=label, plan=plan,
            party_names=party_names, shown_dialogues=shown_dialogues,
            event_order=event_order,
            masker_factory=lambda: npc_identity.build_masker(db, session_id, module),
            npc_guard=_npc_dialogue_guard(db, session_id, game_session, module, llm, player_char),
        )
        await _run_narration_stage(
            db, session_id, game_session, module, player_char, teammates, llm, stream, result,
            plan=plan, events=events, turn_inputs=turn_inputs, party_names=party_names,
            event_order=event_order, base_seq=base_seq, listeners=grp["members"], group=label,
            location_guard=(
                location_guard if player_char.name in (grp.get("members") or []) else ""
            ) + move_note,
        )

    await _apply_coordinator_intents(
        db, session_id, game_session, module, player_char, teammates, llm,
        coordinator_intents,
    )


async def _run_split_generation(
    db: Session,
    session_id: str,
    game_session: GameSession,
    module: Module,
    player_char: Character,
    events: list,
    teammates: list[Character] | None,
    llm,
    rules_enabled: bool,
    matcher_npcs: list[dict],
    groups: list[dict],
    plan: turn_planner.TurnPlan | None = None,
    blind_message: dict | None = None,
    rule_excerpts: list[dict] | None = None,
    location_guard: str = "",
    finish: bool = True,
    combat_before: bool = False,
    move_note: str = "",
) -> None:
    """分头行动：对每个分组各跑一次聚焦的工具循环，后端确定性地把产物归入该组。

    每组单独生成 → 篇幅均衡、不会「只详写最后一个场景」；分组标签由后端注入 →
    前端实时/重连都能稳定分栏，不靠模型自觉打 [GROUP]。工具在各组循环内即时执行。
    ``plan`` 是本回合唯一的裁定计划（跨分组共用），每组都注入一份、也各自校验一次——
    分头场景 NPC/线索并行推进，同样需要 clue_policy/safety 兜底，不能因为分头
    就退化回纯提示词。后置效果与单场景共用 ``_run_post_effects``（常规口径）。
    """
    # 生成前基线序号：供确定性 SAN 守卫判断本轮 KP 是否已自行掷过 SAN（幂等）。
    pre_gen_seq = session_service.get_next_sequence_num(db, session_id) - 1
    await _run_split_narrations_tool_loop(
        db, session_id, game_session, module, player_char, events, teammates,
        llm, rules_enabled, matcher_npcs, groups,
        plan=plan, blind_message=blind_message, rule_excerpts=rule_excerpts,
        location_guard=location_guard, move_note=move_note,
    )
    await _run_post_effects(
        db, session_id, game_session, module, player_char, teammates, llm,
        allowed=turn_pipeline.TurnSpec.for_entry("regular").effects, plan=plan,
        pre_gen_seq=pre_gen_seq, combat_before=combat_before,
    )

    if finish:
        await _finish_generation(db, session_id, llm)


def _skill_names(char: Character) -> list[str]:
    """从角色身上尽可能取出技能名（skills / system_data.skills，兼容 dict 或 list 形态）。"""
    names: set[str] = set()

    def _harvest(obj):
        if isinstance(obj, dict):
            names.update(str(k) for k in obj.keys())
        elif isinstance(obj, list):
            for it in obj:
                if isinstance(it, dict) and it.get("name"):
                    names.add(str(it["name"]))

    _harvest(getattr(char, "skills", None))
    sd = getattr(char, "system_data", None)
    if isinstance(sd, dict):
        _harvest(sd.get("skills"))
    return sorted(names)


_COMBAT_DECLARATION_RE = re.compile(
    r"攻击|袭击|开枪|射击|开火|砍向|劈向|刺向|捅向|挥(?:刀|剑|斧)|"
    r"(?:冲|扑)上去.{0,16}(?:打|揍|攻击|砍|劈|刺)|(?:一拳|一脚|踢向|拳打)"
)


def _looks_like_combat_declaration(text: str) -> bool:
    """高精度识别明确交战宣言，只用于避免被普通检定分诊提前截走。"""
    if re.search(r"(?:不要|别|停止|阻止).{0,6}(?:攻击|袭击|开枪|射击|开火)", text or ""):
        return False
    return bool(_COMBAT_DECLARATION_RE.search(text or ""))


def _stashed_check_request(turn: list, actor_id: str | None) -> str:
    """本轮暂存的技能检定申请（技能页点出来的那种）→ 技能名；没有则空串。

    与暂存的「前往」同一路数：申请时把 skill/intent 落进 metadata，推进时确定性取出，
    不让 planner 再从「（申请「侦查」检定）」这行文本里认一遍——认漏了这次申请就会被当成
    普通叙事顺过去，玩家点的那一下等于没发生。

    同一轮点了多次只认最后一次：那是玩家改主意的自然语义（前一次还能自己删掉）。
    """
    skill = ""
    for ev in turn:
        meta = ev.metadata_ or {}
        if not meta.get("check_request") or not str(meta.get("skill") or "").strip():
            continue
        if actor_id and ev.actor_id and ev.actor_id != actor_id:
            continue        # 多人同桌：只认本轮行动者自己的申请
        skill = str(meta["skill"]).strip()
    return skill


def _failure_notice(exc: BaseException) -> str:
    """把失败原因翻成玩家看得懂的一句话。

    「连不上」和「模型出错了」得分开说：前者玩家自己能处理（查网络、看代理有没有掉），
    后者除了重试没别的办法。都写成「生成中断」的话，玩家只会对着屏幕找自己哪步操作错了。
    """
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        return "（连不上模型服务——请检查网络或代理，然后重试）"
    if isinstance(exc, call_budget.BudgetExceeded):
        return "（本回合 AI 调用已达到预算上限，已停止继续请求；已结算的结果保留。可直接继续输入）"
    if isinstance(exc, StreamStalled):
        return "（模型服务只回心跳不回内容，已放弃等待。多见于中转代理处理长请求出错：到设置页换直连端点后重试）"
    return "（KP 生成中断，请重试或继续输入）"


async def run_chat_generation(session_id: str) -> None:
    # 一个回合是若干 **串行** 的 LLM 环节（等上轮收尾 → 队友 → planner →
    # KP 叙事 → 校验）。单看任何一次调用都不慢，叠起来就是玩家等的那几分钟——所以每一环
    # 都要有耗时，否则「为什么这么慢」只能靠猜。t_turn 给出总时长做对账。
    openai_compat.reset_endpoint_health()   # 见 run_regenerate_generation 里的同一行
    usage_tracker.enter_phase("回合")         # 轨迹 stage 前缀：分得清是哪条路径上的调用
    t_turn = time.monotonic()
    t_drain = time.monotonic()
    await _drain_housekeeping(session_id)
    drain_s = time.monotonic() - t_drain
    if drain_s > 0.5:
        # 上一轮的滚动摘要/幕后推演是「后台」跑的，但下一回合开头要等它写完才能动
        # world_state。玩家手快时就会替上一轮的后台工作买单，且此前完全看不见。
        logger.info("耗时|等上轮收尾 %.1fs session=%s", drain_s, session_id)
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        game_session = db.get(GameSession, session_id)
        module = db.get(Module, game_session.module_id)
        player_char = db.get(Character, game_session.player_character_id)
        ai_teammates = session_service.get_ai_teammates(db, session_id)
        # KP 上下文整队：主角之外的所有已填角色（真人 + AI），让 KP 知道全员在场
        party_others = session_service.get_party_members(
            db, session_id, exclude_id=game_session.player_character_id,
        )
        get_llm()   # fail-fast：未配置 AI 时在此就近报「请到设置页配置」，不深入半截流程
        # 新的玩家回合：上一轮没投的门控收益/线索作废，不能被本轮之后的投骰误兑现。
        check_gating.begin_turn(db, game_session)

        # 一阵疯狂计时：本回合开始给在场角色的临时疯狂发作各减 1 回合，到期自动解除并广播恢复。
        for chunk in _tick_madness_recovery(db, session_id, [player_char, *party_others]):
            room_hub.broadcast(session_id, chunk)

        # 取本轮玩家文本（意图分诊已并入 planner 的 player_check_request 字段，
        # 不再单独跑一次分诊 LLM 调用——省一段串行延迟）。
        turn = _current_turn_events(session_service.get_session_events(db, session_id))
        # 本轮暂存的「前往」动作（大地图前往加入本回合）已随推进转正 → 在建 KP 上下文前
        # 确定性同步该角色所在场景，随后 KP 会以正确位置叙述抵达见闻（无需再单独走一次生成）。
        commit_pending_travel(db, session_id, turn)
        actor_id = next(
            (e.actor_id for e in turn if e.event_type in ("action", "dialogue") and e.actor_id), None,
        )
        acting = (db.get(Character, actor_id) if actor_id else None) or player_char
        player_text = " ".join(
            (e.content or "") for e in turn
            if e.event_type in ("action", "dialogue") and e.actor_id == acting.id and (e.content or "").strip()
        )

        # 玩家本轮明确从技能页申请的检定：先确定性处理，不必等 planner。
        # 这一轮如果还有玩家的台词/行动，队友仍要接话，由 _run_kp_turn 负责在检定提示后跑队友。
        stashed_check = _stashed_check_request(turn, acting.id)
        if (
            stashed_check and player_text
            and not _looks_like_combat_declaration(player_text)
        ):
            await _run_kp_turn(
                db, session_id, game_session, module, player_char, party_others,
                CHECK_REQUEST_PROMPT.format(
                    actor=acting.name, skill=stashed_check, intent=player_text,
                ),
                requested_check=(acting, stashed_check),
                focus_member=acting.name,
                split_aware=True,
                then_team_turn=ai_teammates,
            )
            return

        # 玩家输入后：先跑一轮 AI 队友自动响应（仅 AI 席、仅一轮、不自触发），再交 KP 收束。
        # 队友暗骰（心理学等）的真实结果收集到 team_blind，注入本回合 KP 上下文而不落库/广播。
        team_blind: list[str] = []
        if ai_teammates:
            t_team = time.monotonic(); u_team = usage_tracker.snapshot()
            async for chunk in _run_team_turn(
                # 队友走**主模型**：他们的产出是直接摆在玩家面前的台词与行动，和 KP 叙事一样
                # 吃文笔与分寸感，不是 planner 那种「产结构、玩家看不到」的副任务。快模型
                # （通常关思考、档次也更低）演出来明显发木，同桌感立刻塌掉。慢一点也认。
                db, session_id, game_session, module, player_char, ai_teammates, get_llm(),
                blind_results=team_blind,
            ):
                room_hub.broadcast(session_id, chunk)
            logger.info(
                "耗时|队友回合 %.1fs（%d 人，%s）session=%s",
                time.monotonic() - t_team, len(ai_teammates),
                usage_tracker.fmt(usage_tracker.delta(u_team)), session_id,
            )

        events = session_service.get_session_events(db, session_id)

        # planner 放在队友行动之后：队友不再读 plan，也不再被 KP 的导演计划牵引；
        # 这里只对「最终会发生什么」做一次 KP 裁定，而不是先判、再让队友改、再重判。
        # 结构化的副任务走快模型；没有事件（开场）不跑。
        plan = None
        fast_llm = get_fast_llm()
        if events:
            room_hub.broadcast(session_id, _make_chunk("housekeeping", "守秘人正在判读局势…"))
            t_plan = time.monotonic(); u_plan = usage_tracker.snapshot()
            rules_enabled = rulebook_service.has_rulebook(db, module.rule_system)
            plan_messages = turn_planner.build_turn_plan_messages(
                game_session, module, player_char, events,
                teammates=party_others, rules_lookup_enabled=rules_enabled,
                rule_excerpts=_rule_excerpts_for_planner(db, module, events, game_session),
                rule_options_block=rule_options_service.context_block(db, game_session),
            )
            with agent_trace.span("planner", "规划本回合（队友行动后）", session_id=session_id, db=db):
                plan = await turn_planner.run_turn_planner(fast_llm, plan_messages)
            planned_effects.enforce_plan_item_locations(
                plan, module, game_session.current_scene_id,
            )
            plot_progress.sanitize_plan_flags(plan, module, entity_state.active_flags(game_session))
            logger.info(
                "耗时|planner（队友后） %.1fs（%s）session=%s",
                time.monotonic() - t_plan, usage_tracker.fmt(usage_tracker.delta(u_plan)), session_id,
            )
            if plan is not None:
                _record_clue_ledger_from_plan(
                    db, game_session, plan, events, player_char, party_others,
                    module=module,
                )
                _settle_plan_ending(db, session_id, game_session, module, plan)

        # 玩家直接打字申请检定：planner 从自由文本里识别出来后，仍走确定性检定裁定。
        # 此时队友已经行动过，所以不再重复跑队友；战斗宣言不走此路。
        requested_skill = (stashed_check or (plan.player_check_request if plan else "")).strip()
        if (
            requested_skill and player_text
            and not _looks_like_combat_declaration(player_text)
            and not (plan and plan.combat.should_start)
        ):
            # 计划不能整份丢弃：叙事前效果先落（门控收获暂存，线索已在上面暂存）；
            # 场景/SAN/标志依赖检定结果，留给投骰后的续写裁定，这里不提前执行。
            await _run_pre_effects(
                db, session_id, game_session, player_char, party_others,
                allowed=turn_pipeline.TurnSpec.for_entry("check_request").pre_effects,
                plan=plan, requested_skill=requested_skill, module=module,
            )
            await _run_kp_turn(
                db, session_id, game_session, module, player_char, party_others,
                CHECK_REQUEST_PROMPT.format(
                    actor=acting.name, skill=requested_skill, intent=player_text,
                ),
                requested_check=(acting, requested_skill),
                focus_member=acting.name,
                split_aware=True,
                then_team_turn=None,
            )
            return

        generation_plan = plan

        t_kp = time.monotonic(); u_kp = usage_tracker.snapshot()
        await _run_generation(
            db, session_id, game_session, module, player_char, events,
            teammates=party_others, blind_results=team_blind, plan=generation_plan, finish=False,
        )
        logger.info(
            "耗时|KP 叙事 %.1fs（%s）session=%s",
            time.monotonic() - t_kp, usage_tracker.fmt(usage_tracker.delta(u_kp)), session_id,
        )
        # 叙事之后再判「玩家说了要去、但人没动」：位置可能已被本轮的 [SCENE_CHANGE] 改掉，
        # 先判会对着旧位置问一句「要不要去你已经到了的地方」。
        db.refresh(game_session)
        for chunk in _spoken_travel_intent(
            db, session_id, game_session, module, player_text,
        ):
            room_hub.broadcast(session_id, chunk)
        # 对账用：各环节之和应约等于总时长，对不上说明还有没埋点的环节在吃时间。
        turn_usage = usage_tracker.snapshot()
        logger.info(
            "耗时|本回合合计 %.1fs（%s）session=%s",
            time.monotonic() - t_turn, usage_tracker.fmt(turn_usage), session_id,
        )
        usage_tracker.warn_if_reasoning_dominates(turn_usage)
        await _finish_generation(db, session_id, get_llm())
    except asyncio.CancelledError:
        logger.info("生成被取消: session=%s", session_id)
    except Exception as exc:
        logger.exception("生成失败: session=%s", session_id)
        _persist_error_notice(db, session_id, _failure_notice(exc))
        room_hub.broadcast(session_id, _make_chunk("done"))
    finally:
        db.close()


def _mentioned_scene_id(
    game_session, module, events: list, player_text: str,
) -> str:
    """从玩家本轮文本中确定性地找出「被提到、已知且非当前」的场景 id。

    这是位置守卫与「要不要去」卡片共用的检测口径：只认文本里出现过的场景关键词，
    不判断玩家是否真的移动（移动证据由 ``_explicit_player_movement`` 另行判断）。
    同一轮命中多个地点时只取最后一个——那通常是玩家话里的落点。
    """
    text = (player_text or "").strip()
    if not text or not module:
        return ""
    here = game_session.current_scene_id
    known = session_service.known_scene_ids(module, game_session, events)
    hit = ""
    for scene in (module.scenes or []):
        sid = scene.get("id")
        if not sid or sid == here or sid not in known:
            continue
        if any(kw in text for kw in session_service.scene_unlock_keywords(scene)):
            hit = sid
    return hit


def _location_intent_guard(
    db, session_id: str, game_session, module, player_char: Character,
    events: list, plan: turn_planner.TurnPlan | None,
) -> str:
    """玩家只是提到/打算去 B、但没有确定性移动证据 → 清洗 plan 并生成位置硬约束。

    这一道闸跑在 KP 生成之前。此前只有「生成后才补挂建议卡」和「生成后跳过位置迁移」
    两道事后防线，拦不住模型已经把 B 的所见所闻写进旁白——错误旁白已经广播/落库。
    现在检测到意图后：
      1. 把 plan.scene_policy.scene_change 强制清空，消除计划对模型的误导；
      2. 返回一段最高优先级的系统消息，要求 KP 只演当前地点、不提前描写目标地点。
    如果玩家本轮确有显式移动（地图 travel 或「我走进图书馆」这类明确动作），
    这里不拦——那是真的移动，该由 SCENE_CHANGE 正常切换。
    """
    target = _mentioned_scene_id(game_session, module, events, _turn_player_text(events, player_char))
    return _location_intent_guard_for_target(
        db, session_id, game_session, module, player_char, target, plan,
    )


def _turn_player_text(events: list, player_char: Character) -> str:
    """本轮该玩家角色自己的 action/dialogue 文本（多条合并）。"""
    parts: list[str] = []
    for ev in _current_turn_events(events):
        if (
            ev.actor_id == player_char.id
            and ev.event_type in ("action", "dialogue")
            and (ev.content or "").strip()
        ):
            parts.append(ev.content.strip())
    return " ".join(parts)


def _location_intent_guard_for_target(
    db, session_id: str, game_session, module, player_char: Character,
    target: str, plan: turn_planner.TurnPlan | None,
) -> str:
    """按已检测到的目标场景生成位置硬约束；无目标/已移动/不可达则返回空串。"""
    if not target:
        return ""
    if target == session_service.get_char_location(game_session, player_char.id):
        return ""
    if any(r.get("target") == target for r in movement.turn_records(game_session)):
        return ""   # 本轮移动裁定已就这个目的地给过权威说明（落位 / 待检定 / 受阻）
    if planned_effects._explicit_player_movement(
        db, session_id, module, player_char, target,
    ):
        return ""
    if plan is not None:
        # 模型可能仍把「打算去 B」填成 scene_change；这里在把 plan 交给 KP 前强制收口。
        plan.scene_policy.scene_change = None
    here = session_service.get_char_location(game_session, player_char.id)
    here_title = _scene_name(module, here) or here or "当前场景"
    target_title = _scene_name(module, target) or target
    return (
        "\n\n【位置硬约束——最高优先级，违反即为严重错误】\n"
        f"玩家本轮只是提到或打算去「{target_title}」，人并没有真的移动；"
        f"系统确定的当前位置仍是「{here_title}」。\n"
        "禁止把玩家写成已经到达目标地点：不得描写目标地点的环境、NPC、事件，"
        "也不得写玩家在那里看见、听见、发现或做了任何事。\n"
        "可以写当前地点对这句话的反应（NPC 接话、环境变化），或让 NPC 询问"
        "「是否现在出发」；但真的切换场景只能由玩家确认前往，不能靠旁白替他搬过去。"
    )


def _spoken_travel_intent(
    db, session_id: str, game_session, module, player_text: str,
) -> list:
    """玩家嘴上说了要去某地、却没点大地图 → 挂一张「要不要去」的卡（确定性，不走 LLM）。

    这是本功能最常撞上的那个具体形态：玩家打「我们去图书馆看看」，KP 顺着叙述了一段，
    人却还留在原地——因为场景切换一向要玩家显式发起（杜绝「说句话就被自动搬走」）。
    与其让玩家自己想起来去点地图，不如就地问一句。

    只认**已知且连通**的地点，且只认玩家自己这一轮的文本（KP 的叙述不算——那会变成
    「KP 提一嘴某地就弹卡」，正是要避免的 nag）。已经问过的地方由 travel_suggest_event
    自己去重。同一轮命中多个地点时只问最后一个：那通常是玩家话里的落点。
    """
    text = (player_text or "").strip()
    if not text or not module:
        return []
    events = session_service.get_session_events(db, session_id)
    hit = _mentioned_scene_id(game_session, module, events, text)
    if not hit:
        return []
    chunks, _note = turn_effects.travel_suggest_event(
        db, session_id, game_session, module, hit,
        reason="你刚才提到了这里",
    )
    return chunks


def _group_label_for_focus(
    groups: list[dict],
    focus_member: str | None,
    user_prompt: str,
    requested_check: tuple[Character, str] | None = None,
) -> str:
    """确定分头时本轮聚焦的组：显式 focus_member / requested_check 优先，再按提示词里的成员名兜底。"""
    names: list[str] = []
    if focus_member:
        names.append(focus_member)
    if requested_check is not None:
        names.append(requested_check[0].name)
    for grp in groups:
        for member in (grp.get("members") or []):
            if any(name and (name == member or name in member or member in name) for name in names if name):
                return grp["label"]
    for grp in groups:
        for member in (grp.get("members") or []):
            if member and member in (user_prompt or ""):
                return grp["label"]
    return groups[0]["label"] if groups else ""


#: 申请检定轮里允许保留的旁白长度：一句「她的针脚没有停」当引子无妨，再长就是在替骰子写结果。
_CHECK_REQUEST_LEAD_MAX_CHARS = 60


def _check_request_narration_keep(res: list) -> bool:
    """申请检定轮的 KP 产物要不要保留旁白部分（指令 res[1] 不受影响，照常处理）。

    留：没写旁白；KP 明说这次免检（那就是它的裁定）；旁白很短（一句引子）。
    丢：旁白很长——不管有没有带指令，长旁白都是在骰子落定前替玩家演结果；丢掉之后若也没有
    指令，下游的确定性兜底会补挂检定，玩家点的那一下仍有归宿。
    """
    narration = (res[0] or "").strip()
    if not narration:
        return True
    if any(phrase in narration for phrase in planned_effects._NO_CHECK_PHRASES):
        return True
    if len(narration) <= _CHECK_REQUEST_LEAD_MAX_CHARS:
        return True
    has_directive = bool(
        command_protocol.DICE_CHECK_RE.search(res[1] or "")
        or command_protocol.OPPOSED_CHECK_RE.search(res[1] or "")
    )
    logger.info(
        "申请检定轮丢弃了 %d 字旁白（%s指令）：骰子未定先写揣测，既泄结果又与续写重复",
        len(narration), "带" if has_directive else "无",
    )
    return False


#: 申请检定轮旁白被丢弃时一并丢掉的广播类型：旁白正文与（say/[SAY]）台词气泡。
#: 其余 chunk（检定卡等 loop 内已即时落库的工具事件）照常放出，否则落库与屏幕不一致。
_NARRATION_CHUNK_TYPES = frozenset({"narration", "dialogue"})


async def _run_kp_turn_tool_loop(
    db, session_id, game_session, module, player_char, party_others,
    llm, messages: list, events: list, party_ids: set,
    *, rules_enabled: bool, module_rag_enabled: bool, location_guard: str,
    hold: bool, pre_gen_seq: int,
) -> None:
    """_run_kp_turn 单场景段的工具循环版：生成 + 指令执行 + 终检润色 + 落库。

    与主路径 ``_run_generation`` 的 loop 分支同构（无 plan：旁路回合起点没有裁定计划）；
    ``hold``（申请检定轮）时先攒着 chunk，看完整产物再定旁白去留（见 _check_request_narration_keep）。
    """
    messages = list(messages)
    rs = rule_options_service.ruleset(db, game_session)   # 插件 KP 工具，同主路径
    messages.extend(kp_tool_loop.tool_mode_messages(llm, rs.kp_tools))
    exclude = set()
    if not rules_enabled:
        exclude.add("rule_lookup")
    if not module_rag_enabled:
        exclude.add("module_lookup")
    res = NarrationResult()
    execute = _build_kp_tool_executor(
        db, session_id, game_session, module, player_char, party_others, llm, res,
        pre_gen_seq=pre_gen_seq, ruleset=rs,
    )
    base_seq = session_service.get_next_sequence_num(db, session_id) - 1
    event_order: list = []
    party_names = {player_char.name} | {t.name for t in (party_others or [])}
    stream = _run_kp_agent_loop(
        llm, messages, res, execute,
        tools=kp_tools.openai_tool_schemas(exclude=exclude, extra=rs.kp_tools),
        npcs=_matcher_npcs(module, party_others, game_session), party_names=party_names,
        event_order=event_order,
        masker_factory=lambda: npc_identity.build_masker(db, session_id, module),
        npc_guard=_npc_dialogue_guard(db, session_id, game_session, module, llm, player_char),
    )
    await _run_narration_stage(
        db, session_id, game_session, module, player_char, party_others, llm, stream, res,
        plan=turn_planner.TurnPlan() if location_guard else None,
        events=events, event_order=event_order, base_seq=base_seq,
        party_names=party_names, turn_inputs=_shown_turn_context(events, party_ids),
        location_guard=location_guard, hold=hold,
    )


async def _run_kp_turn(
    db, session_id, game_session, module, player_char, party_others, user_prompt: str,
    then_team_turn: list[Character] | None = None,
    sanity_guard: bool = False,
    mishap_guard: bool = False,
    requested_check: tuple[Character, str] | None = None,
    focus_member: str | None = None,
    split_aware: bool = False,
    finish: bool = True,
) -> None:
    """跑一轮 KP：注入 user_prompt → 流式叙事 → 处理指令（待定检定/掷骰/场景等）→ done。

    ``requested_check=(角色, 技能名)`` 给定时（玩家亲口申请检定的那条路）：叙事结束后确定性
    兜底——玩家点下的那次申请必须有归宿（见 ``planned_effects.requested_check_fallback_command``）。

    ``then_team_turn`` 给定时（如玩家大地图前往后），在 KP 叙事与指令处理之后、``done`` 之前
    再跑一轮 AI 队友回合——否则这条路（不经 run_chat_generation）的队友永远没有发言机会。

    ``sanity_guard`` 给定时（检定后续写等路径）：本函数默认不跑 planner/SAN 守卫，但检定成功
    揭示的恐怖是在**叙事生成时**才出现的（回合起点的 plan 看不到），故在叙事之后现跑一次 planner
    （此时上下文已含刚揭示的恐怖）→ 确定性补发 SAN；KP 已自发掷过 SAN 则幂等跳过、不重复扣。
    """
    # 不支持原生工具调用时经文本指令适配层，同样走工具循环（见 kp_tool_loop.loop_llm）
    llm = kp_tool_loop.loop_llm(get_llm())
    # SAN 守卫基线：本次续写生成前的最大 seq，用于判断 KP 是否已自行掷过 SAN（幂等）。
    pre_gen_seq = session_service.get_next_sequence_num(db, session_id) - 1
    combat_before = bool(combat_service.get_combat(game_session))
    events = session_service.get_session_events(db, session_id)
    # 检定申请/投骰续写等旁路同样可能踩「人在 A、旁白写 B」：这里没有 planner 清洗，
    # 直接复用生成前位置硬闸（plan=None，只注入约束）。
    location_guard = _location_intent_guard(
        db, session_id, game_session, module, player_char, events, None,
    )
    rules_enabled = rulebook_service.has_rulebook(db, module.rule_system)

    # 分头行动：检定申请、投骰续写、前往等旁路同样要按组生成并打分组标签，
    # 不能只演触发者那一组、丢掉其他场景的后续剧情。
    scene_groups = _location_groups(game_session, module, player_char, party_others)
    split_mode = split_aware and len(scene_groups) >= 2
    focus_label: str | None = None
    if split_mode:
        focus_label = _group_label_for_focus(
            scene_groups, focus_member, user_prompt, requested_check,
        )
        group_prompts = {focus_label: user_prompt} if focus_label else None
        await _run_split_narrations_tool_loop(
            db, session_id, game_session, module, player_char, events, party_others,
            llm, rules_enabled, _matcher_npcs(module, party_others, game_session),
            scene_groups, location_guard=location_guard,
            group_prompts=group_prompts,
            default_prompt=SPLIT_FOCUS_PROMPT,
        )
    else:
        module_rag_enabled = getattr(module, "rag_status", "") == "ready"
        party_ids = {player_char.id} | {t.id for t in (party_others or [])}
        messages = build_kp_context(
            game_session, module, player_char, events,
            teammates=party_others, rules_lookup_enabled=rules_enabled, scene_groups=scene_groups,
            module_excerpts=_module_excerpts_for_context(
                db, module, game_session, events, party_ids,
            ),
            module_lookup_enabled=module_rag_enabled,
            recall_enabled=event_recall.is_enabled(game_session),
            rule_options_block=rule_options_service.context_block(db, game_session),
        )
        if location_guard:
            messages.append({"role": "system", "content": location_guard})
        messages.append({"role": "user", "content": user_prompt})

        # 与主路径同一条 agent loop：检定续写 / 申请检定 / 战斗余波 / 前往等旁路也能用
        # rule_lookup / module_lookup / recall_history 等工具，指令在 loop 内执行完毕。
        await _run_kp_turn_tool_loop(
            db, session_id, game_session, module, player_char, party_others,
            llm, messages, events, party_ids,
            rules_enabled=rules_enabled, module_rag_enabled=module_rag_enabled,
            location_guard=location_guard, hold=requested_check is not None,
            pre_gen_seq=pre_gen_seq,
        )

    # 后置效果（旁路口径，见 turn_pipeline.ENTRY_EFFECTS["side"]）：检定后续写最容易写出
    # 「潜行失败 → 它扑上来了」，这条路径回合起点没有 plan，打起来了却没有战斗态全靠补开战；
    # 恐怖揭示 / 大失败身体反噬都是在**叙事生成时**才定的，故按需在叙事后现跑一次 planner
    # 据其裁定确定性补发 SAN / 扣血。KP 已自发掷 SAN / 扣血则各自幂等跳过。
    need_sanity = sanity_guard and not _san_rolled_this_turn(db, session_id, pre_gen_seq)
    need_mishap = mishap_guard and not _hp_changed_this_turn(db, session_id, pre_gen_seq)
    allowed = turn_pipeline.TurnSpec.for_entry("side").effects
    if not need_sanity:
        allowed -= {"sanity"}
    if not need_mishap:
        allowed -= {"mishap"}

    async def post_plan():
        post_events = session_service.get_session_events(db, session_id)
        rules_on = bool(post_events) and rulebook_service.has_rulebook(db, module.rule_system)
        plan_messages = turn_planner.build_turn_plan_messages(
            game_session, module, player_char, post_events, teammates=party_others,
            rules_lookup_enabled=rules_on,
            rule_excerpts=_rule_excerpts_for_planner(db, module, post_events, game_session),
            rule_options_block=rule_options_service.context_block(db, game_session),
        )
        with agent_trace.span("planner", "判理智/反噬（叙事后）", session_id=session_id, db=db):
            plan = await turn_planner.run_turn_planner(get_fast_llm(), plan_messages)
        planned_effects.enforce_plan_item_locations(plan, module, game_session.current_scene_id)
        # 叙事后现跑的这次规划同样看得到刚发生的事：满足剧情钩子就落标志
        plot_progress.sanitize_plan_flags(plan, module, entity_state.active_flags(game_session))
        return plan

    async def requested_fallback():
        cmd = planned_effects.requested_check_fallback_command(
            db, session_id, requested_check[0], requested_check[1], pre_gen_seq,
        )
        if not cmd:
            return
        # 与工具循环同一执行器：分头时限定在触发者那一组，产物打上该组标签。
        focus = next((g for g in scene_groups if g["label"] == focus_label), None) if split_mode else None
        by_name = {c.name: c for c in [player_char, *(party_others or [])]}
        execute = _build_kp_tool_executor(
            db, session_id, game_session, module, player_char, party_others, llm, NarrationResult(),
            pre_gen_seq=pre_gen_seq,
            dice_scope=[by_name[n] for n in (focus or {}).get("members") or [] if n in by_name] or None,
        )
        for call in kp_tool_loop._tool_calls_from_text(cmd):
            outcome = await execute(call)
            for chunk in outcome.chunks:
                if focus_label and split_mode:
                    meta = dict(getattr(chunk, "metadata", None) or {})
                    meta["group"] = focus_label
                    chunk = chunk.model_copy(update={"metadata": meta})
                yield chunk

    await _run_post_effects(
        db, session_id, game_session, module, player_char, party_others, llm,
        allowed=allowed, plan=None, pre_gen_seq=pre_gen_seq, combat_before=combat_before,
        plan_factory=post_plan if (need_sanity or need_mishap) else None,
        requested_check=requested_fallback if requested_check is not None else None,
    )

    if then_team_turn:
        db.refresh(game_session)  # 叙事里可能有 [SCENE_CHANGE]/[MOVE] 改了位置，重取再判分头
        async for chunk in _run_team_turn(
            db, session_id, game_session, module, player_char, then_team_turn, get_llm(),
        ):
            room_hub.broadcast(session_id, chunk)

    if finish:
        await _finish_generation(db, session_id, llm)


async def run_check_request_generation(
    session_id: str, actor_id: str, skill: str, intent: str = "",
) -> None:
    """玩家『申请』检定：交 KP 裁定本次是否需要检定、用什么难度（玩家不指定难度）。

    ``intent`` 是玩家顺带说明的检定目标（如「查书桌暗格」）——现场同时有多条线索/多个
    可疑点时，光报技能名 KP 猜不出具体针对什么，必须带上这句话才能裁定到位。
    KP 若判定需要，会调用 dice_check（或写出 [DICE_CHECK] 经适配层转换），挂成「待玩家投骰」；
    若判定无需检定，则直接简短叙述。"""
    async with _timed(session_id, "申请检定"):
        await _run_check_request_generation(session_id, actor_id, skill, intent)


async def _run_check_request_generation(
    session_id: str, actor_id: str, skill: str, intent: str = "",
) -> None:
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        game_session = db.get(GameSession, session_id)
        module = db.get(Module, game_session.module_id)
        player_char = db.get(Character, game_session.player_character_id)
        actor = db.get(Character, actor_id) or player_char
        party_others = session_service.get_party_members(
            db, session_id, exclude_id=game_session.player_character_id,
        )
        check_gating.begin_turn(db, game_session)   # 新的玩家行动：旧门控暂存作废
        await _run_kp_turn(
            db, session_id, game_session, module, player_char, party_others,
            CHECK_REQUEST_PROMPT.format(
                actor=actor.name, skill=skill,
                intent=intent.strip() or "（未说明，需你结合当前情境自行判断意图）",
            ),
            requested_check=(actor, skill),
            focus_member=actor.name,
            split_aware=True,
        )
    except asyncio.CancelledError:
        logger.info("检定申请生成被取消: session=%s", session_id)
    except Exception as exc:
        logger.exception("检定申请生成失败: session=%s", session_id)
        # 与其他入口一致落库错误提示：只广播的话，done 之后 resync 会把它抹掉。
        _persist_error_notice(db, session_id, _failure_notice(exc))
        room_hub.broadcast(session_id, _make_chunk("done"))
    finally:
        db.close()


async def run_combat_aftermath_generation(session_id: str) -> None:
    """战斗/追逐结束后**主动**生成余波叙述——无需玩家先开口。

    复用既有「combat_result 折回主 KP」通道：build_kp_context 会把结果摘要注入本轮上下文，
    KP 承接直接后果、交代在场者状态、把主动权交还调查员。读一次即清 combat_result，
    避免玩家下一次行动时 _run_generation 再注入一遍余波。无结果摘要 / 无 LLM 则安静收场。
    """
    async with _timed(session_id, "战斗余波"):
        await _run_combat_aftermath_generation(session_id)


async def _run_combat_aftermath_generation(session_id: str) -> None:
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        game_session = db.get(GameSession, session_id)
        if not game_session or not (game_session.world_state or {}).get("combat_result"):
            room_hub.broadcast(session_id, _make_chunk("done"))
            return
        module = db.get(Module, game_session.module_id)
        player_char = db.get(Character, game_session.player_character_id)
        party_others = session_service.get_party_members(
            db, session_id, exclude_id=game_session.player_character_id,
        )
        await _run_kp_turn(
            db, session_id, game_session, module, player_char, party_others,
            COMBAT_AFTERMATH_PROMPT, finish=False, split_aware=True,
        )
        # 读一次即清：_run_kp_turn 走 build_kp_context 注入了 combat_result 但不清除
        # （只有 _run_generation 会清），这里补清，避免下一次玩家回合重复注入余波。
        db.refresh(game_session)
        if (game_session.world_state or {}).get("combat_result"):
            ws = dict(game_session.world_state)
            ws.pop("combat_result", None)
            game_session.world_state = ws
            db.commit()
        await _finish_generation(db, session_id, get_llm())
    except asyncio.CancelledError:
        logger.info("战斗余波生成被取消: session=%s", session_id)
    except Exception:
        logger.exception("战斗余波生成失败: session=%s", session_id)
        room_hub.broadcast(session_id, _make_chunk("done"))
    finally:
        db.close()


async def run_roll_generation(session_id: str, check_id: str, proxy: bool = False) -> None:
    """玩家点『投骰』：取出待定检定 → 按 KP 定的难度掷骰 → 广播达成等级 → KP 据等级续写。

    **顺序要紧**：掷骰是纯确定性引擎调用，一次 LLM 都不需要，所以先把点数掷出来广播给玩家，
    再去等上一轮的后台收尾（滚动摘要 / 幕后推演，都是 LLM 调用）。
    以前是开头就 `await _drain_housekeeping`，玩家点了投骰却先看到「KP 正在整理笔记」、
    要等两次 LLM 才出骰子动画——点得越快等得越久。
    """
    # drain=False：这条路的排干刻意放在掷骰之后（见上），那一段自带打点。
    async with _timed(session_id, "投骰", drain=False):
        await _run_roll_generation(session_id, check_id, proxy)


async def _run_roll_generation(session_id: str, check_id: str, proxy: bool = False) -> None:
    """``proxy``：真人 KP 替缺席玩家代投——骰面注明代投，不弹幸运消费（那得玩家本人决定）。"""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        game_session = db.get(GameSession, session_id)
        # 这里只读不弹：待定检定的**消费**（写 world_state）必须放到 drain 之后，
        # 否则会和 housekeeping 的整体覆盖式写入撞车、丢更新。
        check = session_service.get_pending_check(db, session_id, check_id)
        if not check:
            room_hub.broadcast(session_id, _make_chunk("done"))
            return
        module = db.get(Module, game_session.module_id)
        player_char = db.get(Character, game_session.player_character_id)
        party_others = session_service.get_party_members(
            db, session_id, exclude_id=game_session.player_character_id,
        )

        if check.get("kind") == "san_check":
            char_id = str(check.get("char_id") or "")
            target_char = db.get(Character, char_id)
            if target_char is None:
                await _drain_housekeeping(session_id)
                db.expire_all()
                session_service.pop_pending_check(db, session_id, check_id)
                room_hub.broadcast(session_id, _make_chunk("system", "理智检定角色不存在，已取消"))
                room_hub.broadcast(session_id, _make_chunk("done"))
                return

            source = str(check.get("san_key") or check.get("source") or "")
            # 结算、恐怖源台账与待投卡消费同一笔事务：这些都在 turn_state/台账/角色卡上，
            # 后台收尾只写 world_state，不必等 drain；卡已被并发请求消费则不重复结算。
            san_chunks, san_desc = turn_effects._settle_san_target(
                db,
                session_id,
                game_session,
                target_char,
                str(check.get("success_loss") or "0"),
                str(check.get("failure_loss") or "1d6"),
                source,
                consume_check_id=check_id,
            )
            if not san_chunks:
                room_hub.broadcast(session_id, _make_chunk("done"))
                return
            for chunk in san_chunks:
                room_hub.broadcast(session_id, chunk)

            # 骰子先广播，再等上一轮 housekeeping；之后的批次结果与续写会写 world_state。
            await _drain_housekeeping(session_id)
            db.expire_all()
            game_session = db.get(GameSession, session_id)

            batch_id = str(check.get("san_batch_id") or "")
            remaining = session_service.append_pending_batch_result(
                db, session_id, batch_id, san_desc,
            ) if batch_id else 0
            if remaining:
                room_hub.broadcast(session_id, _make_chunk("done"))
                return

            san_results = [str(item) for item in (check.get("san_results") or [])]
            san_results.append(san_desc)
            desc = "\n".join(san_results)
            if game_session.kp_mode == "human":
                room_hub.broadcast(
                    session_id,
                    _make_chunk(
                        "kp_roll_ready",
                        "理智检定已结算，等待真人 KP 处理后果",
                        metadata={"description": desc},
                    ),
                )
                room_hub.broadcast(session_id, _make_chunk("done"))
                return
            await _run_kp_turn(
                db,
                session_id,
                game_session,
                module,
                player_char,
                party_others,
                KP_DICE_CONTINUATION_PROMPT.format(dice_results=desc),
                focus_member=target_char.name,
                split_aware=True,
            )
            return

        rs = rule_options_service.ruleset(db, game_session)
        skill = rs.check.display_skill_name(check["skill"])
        difficulty = check.get("difficulty", "normal")
        source = check.get("source", "")
        bonus = int(check.get("bonus") or 0)
        penalty = int(check.get("penalty") or 0)
        char_data, disp_name, _is_npc, char_id = _resolve_check_actor(
            check.get("char_ref", ""), skill, player_char, party_others, module,
        )
        # 检定卡是玩家可见面：玩家还没认出来的 NPC 用对外称呼。
        # 真名（disp_name）留给回灌 KP 的描述——守秘人得知道自己在给谁投骰。
        shown_name = npc_identity.build_masker(db, session_id, module)(disp_name)
        # 奖惩骰的来龙去脉：KP 判的理由 + 系统自己加的（临时疯狂），一并摆给玩家看。
        # 只显示最终数量而不说凭什么，玩家就只能猜是不是被针对了。
        modifiers = dice_runtime.modifier_notes(
            bonus, penalty, str(check.get("modifier_reason") or ""),
        )
        # 临时疯狂等系统追加的奖惩骰由规则钩子在检定内部加上（所有检定一视同仁），
        # 理由随结果带回来，与 KP 判的理由一并显示。
        result = rs.check.resolve_skill_check(
            char_data, skill, difficulty, bonus=bonus, penalty=penalty,
        )
        modifiers += result.modifiers
        tier_cn = TIER_LABEL.get(result.tier, result.tier)

        dice_content = (
            f"{shown_name}｜{skill} 检定（{difficulty}）：{tier_cn}（{result.description}）"
        )
        if proxy:
            dice_content = f"KP 代投｜{dice_content}"
        dice_meta = {
            "skill": skill, "skill_value": result.skill_value, "roll": result.roll,
            "target": result.target, "outcome": result.outcome, "tier": result.tier,
            "actor": shown_name, "dice": _check_dice_detail(result, modifiers),
        }
        # 骰子事件与消费待投卡同一笔事务：两者都不碰 world_state（卡在 turn_state），
        # 不必等后台收尾。此前消费放在 drain 之后，中途失败会留下「骰子已出、卡还能再投」——
        # 玩家可以借此重掷一个更好的结果。卡已被并发请求消费则本次不再掷。
        with effect_channel.transaction(db):
            if session_service.pop_pending_check(db, session_id, check_id, commit=False) is None:
                room_hub.broadcast(session_id, _make_chunk("done"))
                return
            ev = session_service.add_event(
                db, session_id, "dice", dice_content, actor_name="系统", metadata=dice_meta,
                commit=False,
            )
        room_hub.broadcast(
            session_id,
            _make_chunk("dice", dice_content, metadata=dice_meta, event_id=ev.id),
        )

        # 骰子已经落地、动画已经在玩家那边跑起来了，现在才等上一轮后台收尾。
        # 但玩家看到的是「骰子出了，然后 KP 迟迟不说话」——这段等待此前一处埋点都没有，
        # 只能靠体感描述。骰子落地到 KP 开口之间的每一段都要能计时。
        t_drain = time.monotonic()
        await _drain_housekeeping(session_id)
        drain_s = time.monotonic() - t_drain
        if drain_s > 0.5:
            logger.info("耗时|投骰后等上轮收尾 %.1fs session=%s", drain_s, session_id)
        # housekeeping 是在另一个 Session 里提交的；本会话的身份映射还挂着旧的
        # world_state，直接写回会把它刚写的摘要/记忆盖掉。expire 掉强制重新取。
        db.expire_all()
        game_session = db.get(GameSession, session_id)

        # 幸运消费（村规开启时）：这一骰差几点够得着？够得着就**停在这里**问玩家买不买。
        # 必须停：后面的物品发货、线索记账、KP 续写都以成败为输入，一旦跑起来就回不了头了。
        offer = rs.luck.rescue_offer(
            result, difficulty, char_data,
            in_combat=bool(combat_service.get_combat(game_session)),
        )
        if offer and char_id and not proxy:
            session_service.set_pending_luck(db, session_id, {
                "check": check,
                "check_id": check_id,
                "char_id": char_id,
                "dice_event_id": ev.id,
                "skill": skill,
                "difficulty": difficulty,
                "disp_name": disp_name,
                "shown_name": shown_name,
                "result": _check_result_payload(result),
                "offer": offer,
            })
            room_hub.broadcast(session_id, _make_chunk(
                "luck_offer",
                f"{shown_name} 的这次检定差 {offer['cost']} 点——可花 {offer['cost']} 点幸运扭转",
                metadata={
                    "char_id": char_id, "actor": shown_name, "skill": skill,
                    "dice_event_id": ev.id, **offer,
                },
                # 与 room_sync 的 luck 快照用同一个 id：前端 addMessage 按 id 幂等，
                # 重连补出来的那张与广播来的那张会合成一条，不会并排出两张卡。
                event_id=luck_offer_event_id(ev.id),
            ))
            room_hub.broadcast(session_id, _make_chunk("done"))
            return

        await _settle_rolled_check(
            db, session_id, game_session, module, player_char, party_others,
            check=check, result=result, skill=skill, difficulty=difficulty,
            source=source, disp_name=disp_name,
        )
    except asyncio.CancelledError:
        logger.info("投骰生成被取消: session=%s", session_id)
        room_hub.broadcast(session_id, _make_chunk("done"))
    except Exception:
        logger.exception("投骰生成失败: session=%s", session_id)
        room_hub.broadcast(session_id, _make_chunk("done"))
    finally:
        db.close()


async def run_luck_decision(session_id: str, spend: bool, reroll: bool = False) -> None:
    """玩家对「要不要动用幸运」拍板后，接着把结算链走完。

    ``spend`` 补差额买下这次成功（官方，规则书 p.85）；``reroll`` 烧固定点数整骰重掷
    （村规）。两者同时给时以 reroll 为准——它是更贵的那个，不该被静默忽略。
    都不选就是放弃。无论哪种，之后都汇回 ``_settle_rolled_check`` 那一条链——买来的成功、
    重掷来的成功和掷出来的成功，后续待遇必须一模一样。
    """
    async with _timed(session_id, "幸运决策", drain=False):
        await _run_luck_decision(session_id, spend, reroll)


async def _run_luck_decision(session_id: str, spend: bool, reroll: bool = False) -> None:
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        pending = session_service.get_pending_luck(db, session_id)
        if not pending:
            room_hub.broadcast(session_id, _make_chunk("done"))
            return
        session_service.set_pending_luck(db, session_id, None)  # 先消费，避免重复提交
        game_session = db.get(GameSession, session_id)
        module = db.get(Module, game_session.module_id)
        player_char = db.get(Character, game_session.player_character_id)
        party_others = session_service.get_party_members(
            db, session_id, exclude_id=game_session.player_character_id,
        )
        check = dict(pending.get("check") or {})
        result = _check_result_from_payload(dict(pending.get("result") or {}))
        offer = dict(pending.get("offer") or {})
        skill = str(pending.get("skill") or "")
        difficulty = str(pending.get("difficulty") or "normal")
        spent = 0

        if spend or reroll:
            char = db.get(Character, str(pending.get("char_id") or ""))
            luck = rule_options_service.ruleset(db, game_session).luck
            char_data = {
                "base_attributes": char.base_attributes if char else {},
                "skills": char.skills if char else {},
                "system_data": char.system_data if char else {},
            }
            # reroll 优先：两个都给时以更贵的那个为准（见本函数文档串）
            cost = int(offer.get("reroll_cost" if reroll else "cost") or 0)
            # 重新校验而不是照单全收：这份 offer 是上一步存下来的，中间幸运值可能已被
            # 别处扣掉（同一轮的另一次消费、KP 手动改卡）。
            if char is not None and cost > 0 and luck.available_luck(char_data) >= cost:
                deduction = luck.deduct(char_data, cost)
                if deduction["path"].startswith("system_data."):
                    turn_effects._update_character_stat(
                        db, char, deduction["path"].split(".", 1)[1], deduction["new"],
                    )
                else:
                    _set_attr_luck(db, char, deduction["new"])
                if reroll:
                    # 整骰重来，结果照单全收（含重掷出大失败）——买的是机会不是成功
                    result = luck.apply_reroll(
                        char_data, skill, difficulty,
                        int(check.get("bonus") or 0), int(check.get("penalty") or 0),
                    )
                else:
                    result = luck.apply_rescue(result, difficulty, cost)
                spent = cost
                _broadcast_luck_applied(
                    db, session_id, pending, result, cost, deduction, reroll=reroll,
                )

        if spent == 0:
            room_hub.broadcast(session_id, _make_chunk(
                "system", f"{pending.get('shown_name') or ''} 没有动用幸运，接受了这次结果。".strip(),
            ))

        await _settle_rolled_check(
            db, session_id, game_session, module, player_char, party_others,
            check=check, result=result, skill=skill, difficulty=difficulty,
            source=str(check.get("source") or ""),
            disp_name=str(pending.get("disp_name") or ""),
            luck_spent=spent,
        )
    except asyncio.CancelledError:
        logger.info("幸运消费后续生成被取消: session=%s", session_id)
        room_hub.broadcast(session_id, _make_chunk("done"))
    except Exception:
        logger.exception("幸运消费结算失败: session=%s", session_id)
        room_hub.broadcast(session_id, _make_chunk("system", "结算出错，请重试"))
        room_hub.broadcast(session_id, _make_chunk("done"))
    finally:
        db.close()


def _set_attr_luck(db, char: Character, value: int) -> None:
    """幸运存在 base_attributes.LUCK 的那一路（AI 生成 / Excel 导入的卡）。"""
    attrs = dict(char.base_attributes or {})
    attrs["LUCK"] = value
    char.base_attributes = attrs
    db.add(char)
    db.commit()


def _broadcast_luck_applied(
    db, session_id: str, pending: dict, result, cost: int, deduction: dict,
    *, reroll: bool = False,
) -> None:
    """把「花了几点、原本掷了多少、现在算什么」摆到台面上，并订正那张结果卡。

    改判必须回到原来那张 dice 事件上：玩家（和重连的人）看的是历史记录，留着一张
    写着「失败」的卡、旁边另起一条「其实成功了」，账就对不上了。

    两种用法要写得能分辨：补差额是「把骰值压下去」，燃运重骰是「换了一骰」——
    后者可能烧了点数反而更糟，账面上得看得出来是重掷不是没生效。
    """
    tier_cn = TIER_LABEL.get(result.tier, result.tier)
    shown_name = str(pending.get("shown_name") or "")
    skill = str(pending.get("skill") or "")
    original = dict(pending.get("result") or {})
    event_id = str(pending.get("dice_event_id") or "")
    how = "烧" if reroll else "花"
    verdict = "重掷得" if reroll else "改判"
    content = (
        f"{shown_name}｜{skill} 检定：{how} {cost} 点幸运（{deduction['old']}→{deduction['new']}），"
        f"{original.get('roll')} → {result.roll}，{verdict} {tier_cn}"
    )
    ev = session_service.add_event(
        db, session_id, "dice", content, actor_name="系统",
        metadata={
            "luck_spend": True, "actor": shown_name, "skill": skill,
            "cost": cost, "luck_before": deduction["old"], "luck_after": deduction["new"],
            "roll_before": original.get("roll"), "roll": result.roll,
            "outcome": result.outcome, "tier": result.tier,
            "target": result.target, "skill_value": result.skill_value,
            "patched_event_id": event_id,
        },
    )
    room_hub.broadcast(session_id, _make_chunk(
        "dice", content, metadata=ev.metadata_, event_id=ev.id,
    ))
    if event_id:
        patch = {
            "outcome": result.outcome, "tier": result.tier,
            "roll": result.roll, "luck_spent": cost,
        }
        room_hub.broadcast(session_id, _make_chunk(
            "event_patch", metadata={"event_id": event_id, "patch": patch},
        ))
        session_service.patch_event_metadata(db, event_id, patch)
    room_hub.broadcast(session_id, _make_chunk("character_update", metadata={
        "char_id": str(pending.get("char_id") or ""),
    }))


def _check_result_payload(result) -> dict:
    """把 CheckResult 摊平成可落 JSON 的字段——待玩家决定花不花幸运期间要存着它。"""
    return {
        "skill_name": result.skill_name, "skill_value": result.skill_value,
        "roll": result.roll, "target": result.target, "outcome": result.outcome,
        "description": result.description, "tier": result.tier,
        "meets_difficulty": result.meets_difficulty,
        "tens": list(result.tens), "tens_kept": result.tens_kept,
        "units": result.units, "bonus": result.bonus, "penalty": result.penalty,
        "modifiers": list(result.modifiers),
    }


def _check_result_from_payload(payload: dict):
    """``_check_result_payload`` 的逆操作。"""
    from app.rules.base import CheckResult

    return CheckResult(**{
        key: payload.get(key)
        for key in (
            "skill_name", "skill_value", "roll", "target", "outcome", "description",
            "tier", "meets_difficulty", "tens", "tens_kept", "units", "bonus", "penalty",
            "modifiers",
        )
        if payload.get(key) is not None
    })


async def _settle_rolled_check(
    db,
    session_id: str,
    game_session: GameSession,
    module: Module,
    player_char: Character,
    party_others: list[Character] | None,
    *,
    check: dict,
    result,
    skill: str,
    difficulty: str,
    source: str,
    disp_name: str,
    luck_spent: int = 0,
) -> None:
    """骰子落定之后的整条结算链：治疗 → 批次归并 → 物品/线索发货 → KP 续写。

    从 ``run_roll_generation`` 里抽出来，是因为幸运消费要在中间插一个「等玩家拍板」的
    断点——买不买都得接着走同一条链，两份拷贝迟早会漂移。
    """
    tier_cn = TIER_LABEL.get(result.tier, result.tier)

    # 治疗类检定成功 → 引擎确定性回血（不靠 KP 自觉发 HP_CHANGE）。广播结算，并把结果并进
    # 回灌 KP 的描述，让 KP 据「已回 N 点」续写而非自己臆断/漏结算。
    heal_note = ""
    heal_target_id = check.get("heal_target_id")
    if heal_target_id:
        target_char = db.get(Character, heal_target_id)
        for chunk in _apply_heal_on_success(db, session_id, target_char, skill, result.outcome):
            room_hub.broadcast(session_id, chunk)
            heal_note = "；系统已按规则确定性结算回血"

    desc = (
        f"{disp_name} {skill}（{difficulty}），达成 {tier_cn}"
        + (f"（针对：{source}）" if source else "")
        + f"：{result.description}{heal_note}"
    )
    succeeded = result.outcome not in ("failure", "fumble")
    fumbled = result.outcome == "fumble"
    # 「先检定、后移动」按人结算，且必须在群检汇总**之前**：用的是这个人自己这一骰的成败，
    # 成功只移动他（续写据新位置叙述）、失败只结束他的尝试；同批其他人照旧待投。
    if check.get("char_id"):
        for chunk in movement.settle_pending_moves(
            db, session_id, game_session, module, [player_char, *(party_others or [])],
            skill=skill, succeeded=succeeded, char_ids={str(check["char_id"])},
        ):
            room_hub.broadcast(session_id, chunk)
    batch_id = str(check.get("check_batch_id") or "")
    if batch_id:
        remaining = session_service.append_pending_group_check_result(
            db,
            session_id,
            batch_id,
            desc,
            succeeded=succeeded,
            fumbled=fumbled,
        )
        if remaining:
            room_hub.broadcast(session_id, _make_chunk("done"))
            return
        batch_results = [str(item) for item in (check.get("check_results") or [])]
        batch_results.append(desc)
        desc = "\n".join(batch_results)
        succeeded = bool(check.get("check_any_success")) or succeeded
        fumbled = bool(check.get("check_any_fumble")) or fumbled
    # 「先检定、后发货」的落地：本轮被这次检定门控的收获此前只暂存未入库，
    # 到这里骰子落地才决定给不给（扒窃掷输了，那块表不该在他包里）。
    for chunk in planned_effects.settle_pending_item_gains(
        db, session_id, game_session, succeeded,
    ):
        room_hub.broadcast(session_id, chunk)
    # 同理「先检定、后记账」：这次检定门控的线索，骰子落地才决定进不进台账。
    planned_effects.settle_pending_clue_reveals(
        db, session_id, game_session, succeeded,
        module=module, on_first_clue=_maybe_clue_illustration,
    )
    if game_session.kp_mode == "human":
        # 真人 KP 模式下掷骰只完成确定性结算，不自动生成后续叙事；KP 可据结果手动发布。
        room_hub.broadcast(
            session_id,
            _make_chunk(
                "kp_roll_ready",
                "群体检定已结算，等待真人 KP 处理后果" if batch_id
                else "检定已结算，等待真人 KP 处理后果",
                metadata={"description": desc},
            ),
        )
        room_hub.broadcast(session_id, _make_chunk("done"))
        return
    # 恐怖多在**检定成功**时才被揭示（看清那具尸体…）；仅成功时才在叙事后补跑 planner
    # 判理智（失败不多花这次调用）。失败若也揭示了恐怖，仍可由 KP 自发 [SAN_CHECK] 兜底。
    # 大失败则可能有**身体反噬**（踢燃烧瓶被烧等）→ 开 mishap 守卫，叙事后据 planner 确定性扣血。
    t_kp = time.monotonic(); u_kp = usage_tracker.snapshot()
    await _run_kp_turn(
        db, session_id, game_session, module, player_char, party_others,
        KP_DICE_CONTINUATION_PROMPT.format(dice_results=desc),
        sanity_guard=succeeded,
        mishap_guard=fumbled,
        focus_member=disp_name,
        split_aware=True,
    )
    logger.info(
        "耗时|投骰后 KP 续写 %.1fs（%s）session=%s",
        time.monotonic() - t_kp, usage_tracker.fmt(usage_tracker.delta(u_kp)), session_id,
    )
    # 合计与思考占比由顶层入口的 _timed 统一记（投骰与幸运决策都汇到这条链，
    # 在这里记会漏掉「掷骰之前」那段，两处入口也会各记一次）。


def _persist_module_intro(db: Session, session_id: str, module: Module) -> str | None:
    """开场前先落一张「背景导语」卡：模组类型/年代/地区/难度/人数 + 一句话前提。

    取自模组作者填写的公开元信息（world_setting / description），不含任何线索或真相，
    给玩家一个「这是个什么故事」的定位，免得直接被拉进场景而摸不着头脑。返回卡片 chunk。
    """
    ws = module.world_setting or {}
    bits: list[str] = []
    for key in ("tone", "era", "region"):
        v = str(ws.get(key) or "").strip()
        if v:
            bits.append(v)
    diff = str(ws.get("difficulty") or "").strip()
    if diff:
        bits.append(f"难度 {diff}")
    pc = str(ws.get("player_count") or "").strip()
    if pc:
        bits.append(f"建议 {pc} 人")
    meta = " · ".join(bits)
    premise = str(module.description or "").strip()
    if not (meta or premise):
        return None
    ev = session_service.add_event(
        db, session_id, "system", premise, actor_name="系统",
        metadata={"kind": "module_intro", "title": module.title or "模组", "meta": meta},
    )
    return event_to_chunk(ev)


async def run_opening_generation(session_id: str) -> None:
    async with _timed(session_id, "开场"):
        await _run_opening_generation(session_id)


async def _run_opening_generation(session_id: str) -> None:
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        game_session = db.get(GameSession, session_id)
        module = db.get(Module, game_session.module_id)
        player_char = db.get(Character, game_session.player_character_id)
        # 幂等：已有正式叙事（旁白/对话）则不重复开场，只收尾。背景导语卡（system）不计入，
        # 这样开场生成中途失败后重试仍能重新生成（而背景卡只补一次、不重复）。
        events_all = session_service.get_session_events(db, session_id)
        if any(e.event_type in ("narration", "dialogue") for e in events_all):
            room_hub.broadcast(session_id, _make_chunk("done"))
            return
        if not any((e.metadata_ or {}).get("kind") == "module_intro" for e in events_all):
            intro_chunk = _persist_module_intro(db, session_id, module)
            if intro_chunk:
                room_hub.broadcast(session_id, intro_chunk)
        party_others = session_service.get_party_members(
            db, session_id, exclude_id=game_session.player_character_id,
        )
        # 开场把各角色卡的静态 equipment 播种进活库存（幂等：库存非空则跳过）。
        for c in [player_char, *party_others]:
            if c:
                inventory_service.seed_from_equipment(db, c)
        # 开场场景配图卡（首入防重靠 scene_cards，开场生成失败重试也只出一张）
        for chunk in _maybe_scene_illustration(
            db, session_id, module, game_session.current_scene_id,
        ):
            room_hub.broadcast(session_id, chunk)
        # 开场不跑队友回合（尚无玩家行动），但把队伍信息带进 KP 上下文让其知道谁在场
        await _run_generation(
            db, session_id, game_session, module, player_char, [],
            teammates=party_others,
        )
    except asyncio.CancelledError:
        logger.info("开场生成被取消: session=%s", session_id)
    except Exception as e:
        logger.exception("开场生成失败: session=%s", session_id)
        # 落库系统提示（而非仅广播）：否则客户端收到 done 后 resync 会把它一并抹掉。
        # 能归类的错误给出可行动原因（如 401→检查 Key），否则回落通用文案。
        hint = _classify_llm_error(e)
        msg = (
            f"（开场生成失败：{hint}。修好后点「重试开场」即可。）"
            if hint else "（开场生成中断，请点「重试开场」或刷新。）"
        )
        _persist_error_notice(db, session_id, msg)
        room_hub.broadcast(session_id, _make_chunk("done"))
    finally:
        db.close()


async def run_epilogue_generation(session_id: str) -> None:
    """模组结束时的**终场收尾**：尾声叙事 + 真相揭晓 + 幕间收束语，一次生成、落成旁白进历史。

    此前「结束模组」只翻个状态、播一行系统提示就没了——玩家刚演完终局，回头看只有一句
    「本模组已结束」，虎头蛇尾。这里补上真正的收场：故事怎么落幕、每个调查员的下场、
    以及他们**没查到**的真相（模组 truth 与线索台账都在 KP 上下文里，散场时正该讲透）。

    与常规回合不同：**不处理任何指令、不跑任何守卫**。收尾就该只是一段叙述——
    不能因为尾声里写了「怪物追来」就又开一场战斗，也不能再挂检定。
    fail-open：生成失败只落一句提示，绝不影响已经结束的会话状态。
    """
    async with _timed(session_id, "尾声"):
        await _run_epilogue_generation(session_id)


async def _run_epilogue_generation(session_id: str) -> None:
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        game_session = db.get(GameSession, session_id)
        if game_session is None:
            return
        ws = game_session.world_state or {}
        if ws.get("epilogue_done"):          # 幂等：一局只收一次场
            room_hub.broadcast(session_id, _make_chunk("done"))
            return
        module = db.get(Module, game_session.module_id)
        player_char = db.get(Character, game_session.player_character_id)
        events = session_service.get_session_events(db, session_id)
        if module is None or player_char is None or not events:
            room_hub.broadcast(session_id, _make_chunk("done"))
            return
        party_others = session_service.get_party_members(
            db, session_id, exclude_id=game_session.player_character_id,
        )
        reached = ws.get("ending_reached") or {}
        ending_line = ""
        if reached.get("name"):
            ending = next(
                (e for e in (getattr(module, "endings", None) or [])
                 if isinstance(e, dict) and str(e.get("id") or "") == str(reached.get("id") or "")),
                {},
            )
            desc = str(ending.get("description") or "").strip()
            ending_line = (
                f"本局抵达的结局是「{reached['name']}」"
                + (f"：{desc}" if desc else "")
                + "。尾声必须落在这个结局上，不要写成别的收场。\n"
            )
        messages = build_kp_context(
            game_session, module, player_char, events, teammates=party_others,
        )
        messages.append({
            "role": "user",
            "content": KP_EPILOGUE_PROMPT.format(ending_line=ending_line),
        })
        room_hub.broadcast(session_id, _make_chunk("generating"))
        res = NarrationResult()
        kp = KPAgent(get_llm())
        stream = _stream_narration_filtered(
            kp, messages, res, npcs=_matcher_npcs(module, party_others, game_session),
            masker=npc_identity.build_masker(db, session_id, module),
            npc_guard=_npc_dialogue_guard(db, session_id, game_session, module, kp, player_char),
        )
        await _run_narration_stage(
            db, session_id, game_session, module, player_char, party_others, kp, stream, res,
            events=events,
        )
        world_state.set_key(db, db.get(GameSession, session_id), "epilogue_done", True)
        # 收场白落定之后再归档经历：这时故事真的讲完了，滚动摘要也已收进最后一批事件，
        # 写小传的素材最全。fail-open——归档失败不影响已经结束的会话。
        await character_chronicle.archive_session(db, session_id)
    except asyncio.CancelledError:
        logger.info("收场生成被取消: session=%s", session_id)
    except Exception:
        logger.exception("收场生成失败: session=%s", session_id)
        _persist_error_notice(
            db, session_id, "（收场叙述生成失败，本模组仍已结束，可直接进行成长结算或查看战报。）",
        )
    finally:
        room_hub.broadcast(session_id, _make_chunk("done"))
        db.close()


async def initialize_human_session(session_id: str) -> None:
    """真人 KP 开局初始化：落公开导语与首场景卡，但绝不调用 AI 生成叙事。"""
    await _drain_housekeeping(session_id)
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        game_session = db.get(GameSession, session_id)
        if not game_session or game_session.kp_mode != "human":
            return
        module = db.get(Module, game_session.module_id)
        if module is None:
            return
        events = session_service.get_session_events(db, session_id)
        if not any((e.metadata_ or {}).get("kind") == "module_intro" for e in events):
            intro = _persist_module_intro(db, session_id, module)
            if intro:
                room_hub.broadcast(session_id, intro)
        for chunk in _maybe_scene_illustration(
            db, session_id, module, game_session.current_scene_id,
        ):
            room_hub.broadcast(session_id, chunk)
        room_hub.broadcast(session_id, _make_chunk("done"))
    except asyncio.CancelledError:
        logger.info("真人 KP 开局初始化被取消: session=%s", session_id)
    except Exception:
        logger.exception("真人 KP 开局初始化失败: session=%s", session_id)
        _persist_error_notice(db, session_id, "（真人 KP 开局初始化中断，请重试）")
        room_hub.broadcast(session_id, _make_chunk("done"))
    finally:
        db.close()


async def run_travel_generation(
    session_id: str, actor_id: str, scene_id: str, via: list[str] | None = None,
) -> None:
    """玩家经大地图『前往』某地：确定性切换该角色所在场景，落「前往」行动，再由 KP 叙述抵达。

    场景切换是后端据玩家显式选择执行的（非 KP 臆测），从根上杜绝「说句话就被自动搬走」。
    ``via``：连通图算出的途经场景名（目标不相邻但连通时非空）——KP 据此叙述穿行而非瞬移。
    """
    async with _timed(session_id, "前往"):
        await _run_travel_generation(session_id, actor_id, scene_id, via)


async def _run_travel_generation(
    session_id: str, actor_id: str, scene_id: str, via: list[str] | None = None,
) -> None:
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        game_session = db.get(GameSession, session_id)
        module = db.get(Module, game_session.module_id)
        player_char = db.get(Character, game_session.player_character_id)
        actor = db.get(Character, actor_id) or player_char
        party_others = session_service.get_party_members(
            db, session_id, exclude_id=game_session.player_character_id,
        )
        ai_teammates = session_service.get_ai_teammates(db, session_id)
        scene = next((s for s in (module.scenes or []) if s.get("id") == scene_id), None)
        scene_name = (scene or {}).get("title") or (scene or {}).get("name") or scene_id

        room_hub.broadcast(session_id, _make_chunk("generating"))
        check_gating.begin_turn(db, game_session)   # 新的玩家行动：旧门控暂存作废
        # 确定性切换该角色位置（主角则一并更新 current_scene_id），并落一条「前往」行动。
        # 与自然语言移动同一套执行语义（movement.travel）：同处的人跟随或被写实在原处。
        movement.travel(
            db, session_id, game_session, module, actor, scene_id,
            [player_char, *[c for c in party_others if c.id != player_char.id]],
        )
        ev = session_service.add_event(
            db, session_id, "action", f"（前往：{scene_name}）",
            actor_id=actor.id, actor_name=actor.name,
        )
        room_hub.broadcast(session_id, event_to_chunk(ev))
        # 首次抵达该场景 → 场景配图卡（先出卡，KP 叙述随后跟上；图片异步补挂）
        for chunk in _maybe_scene_illustration(db, session_id, module, scene_id):
            room_hub.broadcast(session_id, chunk)

        if via:
            passage = "、".join(f"【{v}】" for v in via)
            prompt = (
                f"{actor.name} 从原处出发，途经{passage}，抵达了【{scene_name}】。"
                "途经之处一笔带过（至多点缀一两句沿途见闻，不停留、不触发事件），"
                "再描述抵达地此刻的见闻与气氛，自然承接前文；"
                "不要触发任何检定，也不要替其他玩家角色行动或代言。"
            )
        else:
            prompt = (
                f"{actor.name} 抵达了【{scene_name}】。请描述此地此刻的见闻与气氛，自然承接前文；"
                "不要触发任何检定，也不要替其他玩家角色行动或代言。"
            )
        # 前往后紧接一轮 AI 队友回合：留在原地/另处的队友据「分头」处境各自推进本场景，
        # 不再因为这条路不经 run_chat_generation 而全程哑火。
        await _run_kp_turn(
            db, session_id, game_session, module, player_char, party_others, prompt,
            then_team_turn=ai_teammates,
            focus_member=actor.name,
        )
    except asyncio.CancelledError:
        logger.info("前往生成被取消: session=%s", session_id)
    except Exception:
        logger.exception("前往生成失败: session=%s", session_id)
        _persist_error_notice(db, session_id, "（前往生成中断，请重试）")
        room_hub.broadcast(session_id, _make_chunk("done"))
    finally:
        db.close()


async def run_regenerate_generation(session_id: str) -> None:
    """重新生成最新一轮 KP 叙事：拿本轮玩家与 AI 队友的既有输入、以及已定的骰子作上下文，
    只重跑 KP（不重跑队友回合、不做检定意图分诊），产出新的叙事。

    调用前应已由端点：①取消卡住的旧生成 task；②回滚上一轮 KP 叙事产物
    （session_service.rollback_last_kp_output）。本函数只负责用清理后的事件流重跑 KP。
    """
    # 连不上模型时的熔断只在**一个回合内**有效：它防的是 planner/队友/KP 叙事各等各的
    # 三次重试。玩家点「重新生成」是明确的重试意图，网络多半也是这会儿刚好了，
    # 不清掉的话这一下会被自己的熔断短路——那就成了帮倒忙。
    openai_compat.reset_endpoint_health()
    usage_tracker.enter_phase("重新生成")
    t_turn = time.monotonic()
    t_drain = time.monotonic()
    await _drain_housekeeping(session_id)
    drain_s = time.monotonic() - t_drain
    if drain_s > 0.5:
        logger.info("耗时|重新生成前等上轮收尾 %.1fs session=%s", drain_s, session_id)
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        game_session = db.get(GameSession, session_id)
        module = db.get(Module, game_session.module_id)
        player_char = db.get(Character, game_session.player_character_id)
        party_others = session_service.get_party_members(
            db, session_id, exclude_id=game_session.player_character_id,
        )
        events = session_service.get_session_events(db, session_id)
        t_kp = time.monotonic(); u_kp = usage_tracker.snapshot()
        await _run_generation(
            db, session_id, game_session, module, player_char, events,
            teammates=party_others,
        )
        # 与常规回合同一套打点：「KP 叙事」含生成内 planner、工具循环、校验与编辑器；
        # 各环节之和应约等于合计，对不上就是还有没埋点的地方在吃时间。
        logger.info(
            "耗时|重新生成 KP 叙事 %.1fs（%s）session=%s",
            time.monotonic() - t_kp, usage_tracker.fmt(usage_tracker.delta(u_kp)), session_id,
        )
        turn_usage = usage_tracker.snapshot()
        logger.info(
            "耗时|重新生成合计 %.1fs（%s）session=%s",
            time.monotonic() - t_turn, usage_tracker.fmt(turn_usage), session_id,
        )
        usage_tracker.warn_if_reasoning_dominates(turn_usage)
    except asyncio.CancelledError:
        logger.info("重新生成被取消: session=%s", session_id)
    except Exception:
        logger.exception("重新生成失败: session=%s", session_id)
        _persist_error_notice(db, session_id, "（重新生成中断，请重试）")
        room_hub.broadcast(session_id, _make_chunk("done"))
    finally:
        db.close()


# 兼容既有调用；确定性回合副作用的单一实现位于 turn_effects。
_update_character_stat = turn_effects._update_character_stat
_apply_madness_status = turn_effects._apply_madness_status
_tick_madness_recovery = turn_effects._tick_madness_recovery
_exec_san_check = turn_effects._exec_san_check
_resolve_hp_target = turn_effects._resolve_hp_target
_heal_kind = turn_effects._heal_kind
_infer_heal_target = turn_effects._infer_heal_target
_apply_heal_on_success = turn_effects._apply_heal_on_success
_exec_hp_change = turn_effects._exec_hp_change
_exec_dice_check = turn_effects._exec_dice_check
_auto_roll_check = turn_effects._auto_roll_check
_exec_scene_change = turn_effects._exec_scene_change
_exec_flag = turn_effects._exec_flag
_exec_handout = turn_effects._exec_handout




# 兼容既有调用；真人 KP 工具桌适配位于 human_kp_actions。
execute_human_kp_action = human_kp_actions.execute_human_kp_action







# 兼容既有调用；KP 确定性动作的单一实现位于 kp_actions。
_exec_npc_act = kp_actions._exec_npc_act
_exec_start_chase = kp_actions._exec_start_chase
_exec_start_combat = kp_actions._exec_start_combat
_exec_say = kp_actions._exec_say




# 兼容既有调用；KP 工具协议与循环的单一实现位于 kp_tool_loop。
_rule_lookup_passages = kp_tool_loop._rule_lookup_passages
_module_lookup_passages = kp_tool_loop._module_lookup_passages
MAX_TOOL_LOOP_STEPS = kp_tool_loop.MAX_TOOL_LOOP_STEPS
_tool_loop_active = kp_tool_loop._tool_loop_active
_merge_step_result = kp_tool_loop._merge_step_result
_SOLO_ARG_KEY = kp_tool_loop._SOLO_ARG_KEY
_TEXT_TAG_RE = kp_tool_loop._TEXT_TAG_RE
_tool_call_from_text = kp_tool_loop._tool_call_from_text
_plan_check_call = kp_tool_loop._plan_check_call
_build_kp_tool_executor = kp_tool_loop._build_kp_tool_executor
_run_kp_agent_loop = kp_tool_loop._run_kp_agent_loop
