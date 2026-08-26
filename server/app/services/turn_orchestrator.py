from __future__ import annotations

import asyncio
import logging
import re
import time

from sqlalchemy.orm import Session

from app.ai import turn_planner, usage_tracker
from app.ai import tools as kp_tools
from app.ai.agents.kp_agent import KPAgent
from app.ai.context import build_kp_context
from app.ai.llm_factory import get_fast_llm, get_llm
from app.ai.prompts.kp_system import (
    CHECK_REQUEST_PROMPT,
    COMBAT_AFTERMATH_PROMPT,
    KP_DICE_CONTINUATION_PROMPT,
    KP_EPILOGUE_PROMPT,
)
from app.models.character import Character
from app.models.module import Module
from app.models.session import GameSession
from app.rules.coc import luck as coc_luck
from app.rules.coc import options as coc_options
from app.rules.coc.checks import display_skill_name
from app.rules.registry import get_engine
from app.services import (
    character_chronicle,
    chat_event_writer,
    command_protocol,
    dice_runtime,
    event_protocol,
    event_recall,
    generation_housekeeping,
    generation_lifecycle,
    human_kp_actions,
    illustration_service,
    inventory_service,
    kp_actions,
    kp_tool_loop,
    narration_protocol,
    npc_identity,
    planned_effects,
    combat_service,
    rule_options_service,
    rulebook_service,
    session_service,
    team_turn_service,
    turn_context,
    turn_effects,
    turn_event_order,
    world_state,
)
from app.services.room_hub import room_hub

logger = logging.getLogger(__name__)

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
) -> None:
    """兼容入口；叙事清洗和落库由 chat_event_writer 负责。"""
    chat_event_writer.persist_narration(
        db,
        session_id,
        result,
        event_order,
        attach_npc_portraits=_attach_npc_portraits,
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
) -> None:
    llm = get_llm()
    kp = KPAgent(llm)
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

    # 回合裁定计划：主链路（run_chat_generation）已在队友回合之前先跑好 plan 并记过线索台账，
    # 通过 plan 参数传入 → 此处不重复调用。其他入口（run_travel_generation / _run_kp_turn 尾部）
    # 不传 plan → 这里现跑并记账（钩子 a），行为与前移前完全一致。开场（无事件）不跑。
    if plan is None and events:
        plan_messages = turn_planner.build_turn_plan_messages(
            game_session, module, player_char, events, teammates=teammates,
            rules_lookup_enabled=rules_enabled,
            rule_excerpts=_rule_excerpts_for_planner(db, module, events, game_session),
        )
        plan = await turn_planner.run_turn_planner(get_fast_llm(), plan_messages)
        planned_effects.enforce_plan_item_locations(
            plan, module, game_session.current_scene_id,
        )
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
        # 分头行动 v1 仍走旧正则路径（与 use_tool_calls 开关无关）：多分组编排与
        # loop 的 group 标签/指令归并交互留待下一步，先保证主路径可开关灰度。
        await _run_split_generation(
            db, session_id, game_session, module, player_char, events,
            teammates, kp, llm, rules_enabled, matcher_npcs, scene_groups,
            plan=plan, blind_message=blind_message, rule_excerpts=rule_excerpts,
            location_guard=location_guard,
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

    # 玩家党名单（玩家 + AI 队友）：供台词归属守卫用——KP 绝不能用气泡替他们说话。
    party_names = {player_char.name} | {t.name for t in (teammates or [])}
    result = ["", "", [], [], []]
    if _tool_loop_active(llm):
        # 新路径：agent loop（标准工具调用）。指令在 loop 内经执行器完成（含文本指令兜底），
        # 不再走 _process_commands；validator 终检/落库/记忆钩子与旧路径共用（见下方）。
        messages.append(kp_tools.tool_mode_message())
        exclude = set()
        if not rules_enabled:
            exclude.add("rule_lookup")   # 未挂规则书：不提供该工具（镜像旧路径不广告）
        if not module_rag_enabled:
            exclude.add("module_lookup")  # 原文索引未就绪：同上
        execute = _build_kp_tool_executor(
            db, session_id, game_session, module, player_char, teammates, llm, result,
        )
        # 本轮基线序号 + 事件广播顺序清单：loop 内工具事件即时落库（较小序号），旁白收尾才落库，
        # 直接 resync 会顺序错乱；收尾按广播偏移把本轮（seq>base_seq）事件重排回交错顺序。
        base_seq = session_service.get_next_sequence_num(db, session_id) - 1
        event_order: list = []
        try:
            async for chunk in _run_kp_agent_loop(
                llm, messages, result, execute,
                tools=kp_tools.openai_tool_schemas(exclude=exclude),
                npcs=matcher_npcs, plan=plan, party_names=party_names,
                shown_dialogues=shown_dialogues,
                event_order=event_order,
            ):
                room_hub.broadcast(session_id, chunk)
        except BaseException:
            _persist_narration(db, session_id, result, event_order)
            _reorder_turn_events(db, session_id, event_order, base_seq)
            raise
        _record_turn_usage(db, game_session, llm, events, messages)   # validator 前，趁 last_usage 仍是主叙事那次
        await _validate_and_patch_narration(
            llm, plan, result, event_order, seen_context=_recent_seen_text(events),
            turn_inputs=turn_inputs, on_start=_validator_note(session_id),
            party_names=party_names, location_context=location_guard,
        )
        _persist_narration(db, session_id, result, event_order)
        _reorder_turn_events(db, session_id, event_order, base_seq)
        # 世界记忆钩子 c：本轮 NPC 台词记入其互动史（对全队说话）
        _record_npc_say_memory(
            db, session_id, game_session, module, result[2],
            [player_char.name] + [t.name for t in (teammates or [])],
        )
    else:
        # 旧路径：单次流式生成 + 正则指令后处理（降级开关，行为不变）。
        # 取消（硬取消 task）或流式中途报错（如供应商抖动断流）时，已生成的叙事都要落库，
        # 否则客户端在收到 done 后 resync 会拉到空历史，造成「生成到一半聊天全部消失」。
        try:
            async for chunk in _stream_narration_filtered(
                kp, messages, result, npcs=matcher_npcs, party_names=party_names,
                shown_dialogues=shown_dialogues,
            ):
                room_hub.broadcast(session_id, chunk)
        except BaseException:
            # CancelledError(继承 BaseException) 与普通异常都先把已生成片段落库再上抛
            _persist_narration(db, session_id, result)
            raise
        _record_turn_usage(db, game_session, llm, events, messages)   # validator 前，趁 last_usage 仍是主叙事那次
        await _validate_and_patch_narration(
            llm, plan, result, seen_context=_recent_seen_text(events),
            turn_inputs=turn_inputs, on_start=_validator_note(session_id),
            party_names=party_names, location_context=location_guard,
        )
        _persist_narration(db, session_id, result)
        # 世界记忆钩子 c：本轮 NPC 台词记入其互动史（对全队说话）
        _record_npc_say_memory(
            db, session_id, game_session, module, result[2],
            [player_char.name] + [t.name for t in (teammates or [])],
        )

        async for chunk in _process_commands(
            db, session_id, result[1], module, player_char, game_session, llm,
            teammates=teammates, pre_gen_seq=pre_gen_seq,
        ):
            room_hub.broadcast(session_id, chunk)

    async for chunk in _ensure_planned_combat(
        db, session_id, game_session, module, player_char, teammates, llm, plan,
    ):
        room_hub.broadcast(session_id, chunk)

    # 叙事—机制脱节守卫：计划判了不开战，KP 却把怪物写成已经扑上来 → 补开战（预筛后才判）。
    async for chunk in _ensure_narrated_combat(
        db, session_id, game_session, module, player_char, teammates, llm, plan, pre_gen_seq,
    ):
        room_hub.broadcast(session_id, chunk)

    # 确定性场景守卫先执行：真实进入目标场景后，后续 SAN 守卫才能读取该场景的模组机制。
    async for chunk in _ensure_planned_scene(
        db, session_id, game_session, module, player_char, teammates, plan,
        pre_gen_seq=pre_gen_seq,
    ):
        room_hub.broadcast(session_id, chunk)

    # 确定性进场检定守卫：模组写明「进入本场景时投X」的机制点 → 后端补发（每角色一次）。
    # 开场不跑规划器、又禁止 KP 发检定，起始场景的进场检定只能靠这里补。
    async for chunk in _ensure_scene_entry_checks(
        db, session_id, game_session, module, player_char, teammates,
    ):
        room_hub.broadcast(session_id, chunk)

    # 确定性 SAN 守卫：计划裁定本轮目睹恐怖但 KP 漏发 SAN → 后端补发（幂等）。
    async for chunk in _ensure_planned_sanity(
        db, session_id, game_session, player_char, teammates, plan, pre_gen_seq,
        module=module,
    ):
        room_hub.broadcast(session_id, chunk)

    # 确定性库存守卫：计划裁定的物品获得/失去 → 后端确定性增减（幂等），库存是权威状态。
    async for chunk in _ensure_planned_items(
        db, session_id, game_session, player_char, teammates, plan,
    ):
        room_hub.broadcast(session_id, chunk)

    # 确定性战斗伤害守卫：战斗中非常规/范围攻击 → 挂成玩家 pending_roll 亲手掷、扣敌人 HP。
    async for chunk in _ensure_planned_combat_damage(db, session_id, player_char, plan):
        room_hub.broadcast(session_id, chunk)

    # 叙事进度记账（放在全部守卫之后：场景守卫可能刚把队伍挪进新场景）：本轮演过的场景
    # 机制点 / 摆到玩家面前的线索 → 确定性写世界记忆，补规划器漏记的那些（详见守卫文档）。
    planned_effects.record_narrated_progress(
        db, session_id, game_session, module, player_char, teammates, pre_gen_seq,
    )

    await _finish_generation(db, session_id, llm)


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


async def _run_split_narrations(
    db: Session,
    session_id: str,
    game_session: GameSession,
    module: Module,
    player_char: Character,
    events: list,
    teammates: list[Character] | None,
    kp: KPAgent,
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
) -> str:
    """分头行动：逐组生成聚焦叙事，后端确定性归组并落库。

    返回各组成员叙事合并后的指令文本（供统一 _process_commands 处理）。
    ``group_prompts`` 可按组覆盖提示词（如检定裁定/骰子续写只喂给相关组）；
    未覆盖的组使用 ``default_prompt``（缺省 SPLIT_FOCUS_PROMPT）继续推进本场景。
    """
    # 先把本回合各角色的行动/对话/掷骰也归入其所在场景列：这样每一列＝该场景里
    # 「玩家行动 + KP 叙事」自成一体（而非行动全挤在主线、叙事另起一列）。
    # 位置已由显式移动（玩家大地图 / 队友 travel 动作）确定性写入，此处不再据分组反推搬人。
    _tag_turn_events_by_group(db, _current_turn_events(events), groups)
    plan_message = turn_planner.build_turn_plan_message(plan) if plan is not None else None

    # 模组原文 RAG：与单场景路径同一门槛（索引就绪才广告 [MODULE_LOOKUP]/注入摘录）
    module_rag_enabled = bool(events) and getattr(module, "rag_status", "") == "ready"
    party_ids = {player_char.id} | {t.id for t in (teammates or [])}
    shown_dialogues = _shown_turn_dialogues(events, party_ids)
    turn_inputs = _shown_turn_context(events, party_ids)

    combined: list[str] = []
    for grp in groups:
        label = grp["label"]
        members = "、".join(grp["members"])
        # 关键：以该组所在场景为锚构建上下文，否则每列都拿主角场景的 NPC/线索，
        # KP 只能把主角场景重复叙述一遍（两列讲同一件事）。
        messages = build_kp_context(
            game_session, module, player_char, events, teammates=teammates,
            rules_lookup_enabled=rules_enabled, viewer_scene_id=grp.get("scene_id"),
            module_excerpts=_module_excerpts_for_context(
                db, module, game_session, events, party_ids,
                scene_id=grp.get("scene_id"),
            ),
            module_lookup_enabled=module_rag_enabled,
            # 规则要点不依赖场景：各分组共用调用方预取的同一份（与模组摘录注入现状对齐）
            rule_excerpts=rule_excerpts,
            recall_enabled=event_recall.is_enabled(game_session),
            # 全部分组都给，配合 viewer_scene_id 指明本轮聚焦哪一组：KP 得知道别组的人
            # 此刻不在场，才不会把他们写进这一列。
            scene_groups=groups,
        )
        if plan_message is not None:
            messages.append(plan_message)
        if blind_message is not None:
            messages.append(blind_message)
        if location_guard and player_char.name in (grp.get("members") or []):
            messages.append({"role": "system", "content": location_guard})
        if group_prompts and label in group_prompts:
            user_content = group_prompts[label]
        else:
            prompt = default_prompt or SPLIT_FOCUS_PROMPT
            user_content = prompt.format(label=label, members=members)
        messages.append({"role": "user", "content": user_content})
        result = ["", "", [], [], []]
        try:
            stream_kwargs = {"shown_dialogues": shown_dialogues} if shown_dialogues else {}
            async for chunk in _stream_narration_filtered(
                kp, messages, result, npcs=matcher_npcs, group_label=label,
                **stream_kwargs,
            ):
                room_hub.broadcast(session_id, chunk)
        except BaseException:
            _persist_narration(db, session_id, result)
            raise
        await _validate_and_patch_narration(
            llm, plan, result, seen_context=_recent_seen_text(events),
            turn_inputs=turn_inputs, on_start=_validator_note(session_id),
            # 取**全队**而非本组成员：别组的队友同样不能被代演
            party_names={player_char.name} | {t.name for t in (teammates or [])},
            location_context=(
                location_guard if player_char.name in (grp.get("members") or []) else ""
            ),
        )
        _persist_narration(db, session_id, result)
        # 世界记忆钩子 c：本组 NPC 台词记入其互动史（听众＝该组成员，信息不跨组共享）
        _record_npc_say_memory(
            db, session_id, game_session, module, result[2], grp["members"],
        )
        # 留下组界标记：合并后的文本交给 _process_commands 统一处理，指令本身不带出处，
        # 只有这个标记能让「谁该跟着检定」落回发出指令的那一组（见 _group_scope_resolver）。
        combined.append(f"[GROUP: scene={label}]\n{result[1]}")
    return "\n".join(combined)


async def _run_split_generation(
    db: Session,
    session_id: str,
    game_session: GameSession,
    module: Module,
    player_char: Character,
    events: list,
    teammates: list[Character] | None,
    kp: KPAgent,
    llm,
    rules_enabled: bool,
    matcher_npcs: list[dict],
    groups: list[dict],
    plan: turn_planner.TurnPlan | None = None,
    blind_message: dict | None = None,
    rule_excerpts: list[dict] | None = None,
    location_guard: str = "",
) -> None:
    """分头行动：对每个分组各跑一次聚焦叙事，后端确定性地把产物归入该组。

    每组单独生成 → 篇幅均衡、不会「只详写最后一个场景」；分组标签由后端注入 →
    前端实时/重连都能稳定分栏，不靠模型自觉打 [GROUP]。
    命令（检定/HP/旗标/场景）在所有分组叙事完成后，对合并文本统一处理一次。
    ``plan`` 是本回合唯一的裁定计划（跨分组共用），每组都注入一份、也各自校验一次——
    分头场景 NPC/线索并行推进，同样需要 clue_policy/safety 兜底，不能因为分头
    就退化回纯提示词。
    """
    # 生成前基线序号：供确定性 SAN 守卫判断本轮 KP 是否已自行掷过 SAN（幂等）。
    pre_gen_seq = session_service.get_next_sequence_num(db, session_id) - 1
    combined = await _run_split_narrations(
        db, session_id, game_session, module, player_char, events, teammates,
        kp, llm, rules_enabled, matcher_npcs, groups,
        plan=plan, blind_message=blind_message, rule_excerpts=rule_excerpts,
        location_guard=location_guard,
    )

    async for chunk in _process_commands(
        db, session_id, combined, module, player_char, game_session, llm,
        teammates=teammates, scene_groups=groups, pre_gen_seq=pre_gen_seq,
    ):
        room_hub.broadcast(session_id, chunk)

    async for chunk in _ensure_planned_combat(
        db, session_id, game_session, module, player_char, teammates, llm, plan,
    ):
        room_hub.broadcast(session_id, chunk)

    # 叙事—机制脱节守卫：分头行动同样可能被某一组写出交战却没落成战斗态。
    async for chunk in _ensure_narrated_combat(
        db, session_id, game_session, module, player_char, teammates, llm, plan, pre_gen_seq,
    ):
        room_hub.broadcast(session_id, chunk)

    # 先落实有真实移动证据的场景，再据抵达场景的模组机制裁定 SAN。
    async for chunk in _ensure_planned_scene(
        db, session_id, game_session, module, player_char, teammates, plan,
        pre_gen_seq=pre_gen_seq,
    ):
        room_hub.broadcast(session_id, chunk)

    # 分头行动同样补进场检定：各分组按自己所在场景各判各的（幂等键逐角色）。
    async for chunk in _ensure_scene_entry_checks(
        db, session_id, game_session, module, player_char, teammates,
    ):
        room_hub.broadcast(session_id, chunk)

    # 确定性 SAN 守卫：计划裁定本轮目睹恐怖但 KP 漏发 SAN → 后端补发（幂等）。
    async for chunk in _ensure_planned_sanity(
        db, session_id, game_session, player_char, teammates, plan, pre_gen_seq,
        module=module,
    ):
        room_hub.broadcast(session_id, chunk)

    # 确定性库存守卫：计划裁定的物品获得/失去 → 后端确定性增减（幂等），库存是权威状态。
    async for chunk in _ensure_planned_items(
        db, session_id, game_session, player_char, teammates, plan,
    ):
        room_hub.broadcast(session_id, chunk)

    # 确定性战斗伤害守卫：战斗中非常规/范围攻击 → 挂成玩家 pending_roll 亲手掷、扣敌人 HP。
    async for chunk in _ensure_planned_combat_damage(db, session_id, player_char, plan):
        room_hub.broadcast(session_id, chunk)

    # 叙事进度记账（放在全部守卫之后：场景守卫可能刚把队伍挪进新场景）：本轮演过的场景
    # 机制点 / 摆到玩家面前的线索 → 确定性写世界记忆，补规划器漏记的那些（详见守卫文档）。
    planned_effects.record_narrated_progress(
        db, session_id, game_session, module, player_char, teammates, pre_gen_seq,
    )

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


def _team_guidance_from_plan(plan: turn_planner.TurnPlan | None) -> str:
    """从 plan.direction 派生给 AI 队友的软指引（目前只用 spotlight——把戏份让给冷场玩家）。

    只影响队友「优先照顾谁」，不授权队友替人决定/代言；无 plan 或无 spotlight 则为空串。
    """
    if plan is None or not plan.direction.spotlight:
        return ""
    return (
        "本轮请把互动机会和话头多留给："
        + "、".join(plan.direction.spotlight)
        + "（他们最近戏份偏少）。你仍然只能决定自己的言行，不得替他们做决定或代言。"
    )


#: 队友这一轮产出了这些类型的事件，就说明裁定前提变了，必须重新规划。
#: dialogue（对白）**不在其中**——这是本判据的全部要点，理由见下。
_PREMISE_CHANGING_EVENT_TYPES = frozenset(
    ("action", "dice", "system", "combat", "scene_change"),
)


def _team_turn_changed_premises(events: list, ai_teammates: list, pre_seq: int) -> bool:
    """队友这一轮是否改变了裁定前提——决定要不要再跑一次 planner。

    planner 裁定的是**玩家这一轮宣言**该怎么判（要不要检定、什么难度、给什么线索）。队友
    只是接了句话时，这些前提一个都没变，重新裁定一遍纯属浪费——实测那一次要 46 秒，占整个
    回合的 44%（两个队友分别说了「背面也写了字？让我看看」和「便签背面写了什么？」，
    planner 于是对着一模一样的前提把「扯下便签」重判了一遍）。

    但队友真动手就不一样了：他挪了位置、开了箱子、掷了骰、触发了机制点，玩家动作的判定
    前提就真的变了，必须重规划。

    判错的代价不对称：漏判（该重规划却跳过）会让 KP 拿着过时的裁定叙事；多判只是慢几十秒。
    所以这里**从宽认定「变了」**——除对白外的任何新事件都算，拿不准就重规划。
    """
    if not ai_teammates:
        return False
    mate_ids = {t.id for t in ai_teammates}
    mate_names = {(t.name or "").strip() for t in ai_teammates if (t.name or "").strip()}
    for e in events:
        if (e.sequence_num or 0) <= pre_seq:
            continue                                  # 队友开口前就有的，不算
        by_mate = (getattr(e, "actor_id", None) in mate_ids
                   or (getattr(e, "actor_name", "") or "").strip() in mate_names)
        # 系统事件（掷骰、入库、场景切换）不带队友署名，但同样是队友行动的产物 → 一律算。
        if e.event_type in _PREMISE_CHANGING_EVENT_TYPES and (by_mate or e.event_type != "action"):
            return True
    return False


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


async def run_chat_generation(session_id: str) -> None:
    # 一个回合是若干 **串行** 的 LLM 环节（等上轮收尾 → planner → 队友 → 二次 planner →
    # KP 叙事 → 校验）。单看任何一次调用都不慢，叠起来就是玩家等的那几分钟——所以每一环
    # 都要有耗时，否则「为什么这么慢」只能靠猜。t_turn 给出总时长做对账。
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

        # planner 前移：在队友回合之前先跑一次裁定计划，作为本回合的共享契约——队友据
        # plan.direction 派生的导演提示行动（如把话头递给冷场玩家），KP 叙事时再以队友实际
        # 行动 + plan 为准。plan 是「裁定意图」不是「剧本」，队友行动后语义不变；开场不跑。
        # 结构化副任务走快模型（get_fast_llm，未配置时即主模型）。
        pre_events = session_service.get_session_events(db, session_id)
        plan = None
        fast_llm = get_fast_llm()
        if pre_events:
            room_hub.broadcast(session_id, _make_chunk("housekeeping", "守秘人正在判读局势…"))
            t_plan = time.monotonic(); u_plan = usage_tracker.snapshot()
            rules_enabled = rulebook_service.has_rulebook(db, module.rule_system)
            plan_messages = turn_planner.build_turn_plan_messages(
                game_session, module, player_char, pre_events,
                teammates=party_others, rules_lookup_enabled=rules_enabled,
                rule_excerpts=_rule_excerpts_for_planner(db, module, pre_events, game_session),
            )
            plan = await turn_planner.run_turn_planner(fast_llm, plan_messages)
            planned_effects.enforce_plan_item_locations(
                plan, module, game_session.current_scene_id,
            )
            logger.info(
                "耗时|planner %.1fs（%s）session=%s",
                time.monotonic() - t_plan, usage_tracker.fmt(usage_tracker.delta(u_plan)), session_id,
            )
            # 世界记忆钩子 a：本轮裁定要揭示线索 → 写入线索台账（前移后在此统一记账）
            if plan is not None:
                _record_clue_ledger_from_plan(
                    db, game_session, plan, pre_events, player_char, party_others,
                    module=module,
                )
                _settle_plan_ending(db, session_id, game_session, module, plan)

        # 玩家明确申请检定 → 直接走确定性检定裁定（避免被 KP 当叙事顺过去），不走常规叙事。
        # 战斗宣言不走此路。
        #
        # 技能页点出来的申请带 check_request 元数据，**优先据此确定性取技能名**：那是玩家点
        # 明白了的，不该再让 planner 从「（申请「侦查」检定）」这行文本里认一遍——认漏了这次
        # 申请就被当成普通叙事顺过去，玩家点的那一下等于没发生。planner 的
        # player_check_request 仍作为「玩家直接打字申请」的兜底。
        stashed_check = _stashed_check_request(turn, acting.id)
        requested_skill = (stashed_check or (plan.player_check_request if plan else "")).strip()
        if (
            requested_skill and player_text
            and not _looks_like_combat_declaration(player_text)
            and not (plan and plan.combat.should_start)
        ):
            await _run_kp_turn(
                db, session_id, game_session, module, player_char, party_others,
                CHECK_REQUEST_PROMPT.format(
                    actor=acting.name, skill=requested_skill, intent=player_text,
                ),
                requested_check=(acting, requested_skill),
                focus_member=acting.name,
                split_aware=True,
                # 暂存式申请：这一轮里还有玩家的台词与行动，队友得像常规回合那样接话——
                # 否则「说一句 + 顺手查一下」会让队友从申请一路哑到投骰结果之后（这条路和
                # 投骰续写都不跑队友回合）。打字申请的老路径维持原样，不跑队友。
                then_team_turn=ai_teammates if stashed_check else None,
            )
            return

        # 玩家输入后：先跑一轮 AI 队友自动响应（仅 AI 席、仅一轮、不自触发），再交 KP 收束。
        # 队友暗骰（心理学等）的真实结果收集到 team_blind，注入本回合 KP 上下文而不落库/广播。
        team_blind: list[str] = []
        # 记下队友开口前的进度线，之后据此只看「队友这一轮新产生的事件」。
        pre_seq = max((e.sequence_num or 0 for e in pre_events), default=0)
        if ai_teammates:
            t_team = time.monotonic(); u_team = usage_tracker.snapshot()
            async for chunk in _run_team_turn(
                # 队友走**主模型**：他们的产出是直接摆在玩家面前的台词与行动，和 KP 叙事一样
                # 吃文笔与分寸感，不是 planner 那种「产结构、玩家看不到」的副任务。快模型
                # （通常关思考、档次也更低）演出来明显发木，同桌感立刻塌掉。慢一点也认。
                db, session_id, game_session, module, player_char, ai_teammates, get_llm(),
                blind_results=team_blind,
                team_guidance=_team_guidance_from_plan(plan),
            ):
                room_hub.broadcast(session_id, chunk)
            logger.info(
                "耗时|队友回合 %.1fs（%d 人，%s）session=%s",
                time.monotonic() - t_team, len(ai_teammates),
                usage_tracker.fmt(usage_tracker.delta(u_team)), session_id,
            )

        events = session_service.get_session_events(db, session_id)
        # 队友行动可能补充真实移动、恐怖见闻或新的行动事实；回合起点的 plan 只用于
        # 队友导演提示，不能继续作为最终副作用裁定。重新规划后的结果才交给 KP 与守卫。
        generation_plan = plan
        if ai_teammates and _team_turn_changed_premises(events, ai_teammates, pre_seq):
            t_post = time.monotonic(); u_post = usage_tracker.snapshot()
            db.refresh(game_session)
            post_rules_enabled = rulebook_service.has_rulebook(db, module.rule_system)
            post_plan_messages = turn_planner.build_turn_plan_messages(
                game_session, module, player_char, events,
                teammates=party_others,
                rules_lookup_enabled=post_rules_enabled,
                rule_excerpts=_rule_excerpts_for_planner(db, module, events, game_session),
            )
            post_plan = await turn_planner.run_turn_planner(fast_llm, post_plan_messages)
            # 有 AI 队友的局，planner 一个回合要跑**两次**（队友行动会改变裁定前提）。
            # 这是队友局比单人局明显慢的主因之一，必须单独计时才看得见。
            logger.info(
                "耗时|二次 planner %.1fs（%s）session=%s",
                time.monotonic() - t_post, usage_tracker.fmt(usage_tracker.delta(u_post)), session_id,
            )
            if post_plan is not None:
                generation_plan = post_plan
                planned_effects.enforce_plan_item_locations(
                    generation_plan, module, game_session.current_scene_id,
                )
                _record_clue_ledger_from_plan(
                    db, game_session, generation_plan, events, player_char, party_others,
                    module=module,
                )
                _settle_plan_ending(db, session_id, game_session, module, generation_plan)
        elif ai_teammates:
            logger.info(
                "耗时|二次 planner 已跳过（队友本轮只有对白）session=%s", session_id,
            )
        t_kp = time.monotonic(); u_kp = usage_tracker.snapshot()
        await _run_generation(
            db, session_id, game_session, module, player_char, events,
            teammates=party_others, blind_results=team_blind, plan=generation_plan,
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
    except asyncio.CancelledError:
        logger.info("生成被取消: session=%s", session_id)
    except Exception:
        logger.exception("生成失败: session=%s", session_id)
        _persist_error_notice(db, session_id, "（KP 生成中断，请重试或继续输入）")
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


async def _run_kp_turn(
    db, session_id, game_session, module, player_char, party_others, user_prompt: str,
    then_team_turn: list[Character] | None = None,
    sanity_guard: bool = False,
    mishap_guard: bool = False,
    requested_check: tuple[Character, str] | None = None,
    focus_member: str | None = None,
    split_aware: bool = False,
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
    llm = get_llm()
    kp = KPAgent(llm)
    # SAN 守卫基线：本次续写生成前的最大 seq，用于判断 KP 是否已自行掷过 SAN（幂等）。
    pre_gen_seq = session_service.get_next_sequence_num(db, session_id) - 1
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
        commands_text = await _run_split_narrations(
            db, session_id, game_session, module, player_char, events, party_others,
            kp, llm, rules_enabled, _matcher_npcs(module, party_others, game_session),
            scene_groups, location_guard=location_guard,
            group_prompts=group_prompts,
            default_prompt=SPLIT_FOCUS_PROMPT,
        )
    else:
        module_rag_enabled = getattr(module, "rag_status", "") == "ready"
        party_ids = {player_char.id} | {t.id for t in (party_others or [])}
        messages = build_kp_context(
            game_session, module, player_char, events,
            teammates=party_others, rules_lookup_enabled=rules_enabled,
            module_excerpts=_module_excerpts_for_context(
                db, module, game_session, events, party_ids,
            ),
            module_lookup_enabled=module_rag_enabled,
            recall_enabled=event_recall.is_enabled(game_session),
        )
        if location_guard:
            messages.append({"role": "system", "content": location_guard})
        messages.append({"role": "user", "content": user_prompt})

        res = ["", "", [], [], []]
        try:
            async for chunk in _stream_narration_filtered(
                kp, messages, res, npcs=_matcher_npcs(module, party_others, game_session),
            ):
                room_hub.broadcast(session_id, chunk)
        except asyncio.CancelledError:
            _persist_narration(db, session_id, res)
            raise
        if location_guard:
            # 旁路没有完整 plan，仍用位置硬约束对落库版本做一次定点终检。
            validation = await turn_context.turn_validator.validate_turn_narration(
                llm, turn_planner.TurnPlan(), res[0],
                turn_inputs=_shown_turn_context(events, party_ids),
                party_names={player_char.name} | {t.name for t in (party_others or [])},
                location_context=location_guard,
            )
            if validation is not None and validation.violated:
                logger.warning("旁路位置终检已改写落库旁白：%s", validation.reason)
                res[0] = validation.corrected_narration
        _persist_narration(db, session_id, res)
        # 世界记忆钩子 c：本轮 NPC 台词记入其互动史（对全队说话）
        _record_npc_say_memory(
            db, session_id, game_session, module, res[2],
            [player_char.name] + [t.name for t in (party_others or [])],
        )
        commands_text = res[1]

    async for chunk in _process_commands(
        db, session_id, commands_text, module, player_char, game_session, llm,
        teammates=party_others,
        scene_groups=scene_groups if split_mode else None,
        focus_group_label=focus_label,
        pre_gen_seq=pre_gen_seq,
    ):
        room_hub.broadcast(session_id, chunk)

    # 叙事—机制脱节守卫：检定后续写最容易写出「潜行失败 → 它扑上来了」，而这条路径回合
    # 起点根本没有 plan（开战裁定无从谈起），叙事里打起来了却没有战斗态就全靠这里兜。
    async for chunk in _ensure_narrated_combat(
        db, session_id, game_session, module, player_char, party_others, llm, None, pre_gen_seq,
    ):
        room_hub.broadcast(session_id, chunk)

    # 大地图『前往』走的就是这条路：抵达新场景后立刻补该场景的进场检定，不必等下一轮行动。
    async for chunk in _ensure_scene_entry_checks(
        db, session_id, game_session, module, player_char, party_others,
    ):
        room_hub.broadcast(session_id, chunk)

    # 确定性后果守卫（检定后续写等路径）：恐怖揭示 / 大失败身体反噬都是在**叙事生成时**才定的
    # （回合起点的 plan 看不到），故在叙事之后现跑一次 planner——此时上下文已含刚揭示的恐怖与
    # 大失败结果——据其 sanity / mishap 裁定确定性补发 SAN / 扣血。KP 已自发掷 SAN / 扣血则各自幂等跳过。
    need_sanity = sanity_guard and not _san_rolled_this_turn(db, session_id, pre_gen_seq)
    need_mishap = mishap_guard and not _hp_changed_this_turn(db, session_id, pre_gen_seq)
    if need_sanity or need_mishap:
        post_events = session_service.get_session_events(db, session_id)
        rules_enabled = bool(post_events) and rulebook_service.has_rulebook(db, module.rule_system)
        plan_messages = turn_planner.build_turn_plan_messages(
            game_session, module, player_char, post_events, teammates=party_others,
            rules_lookup_enabled=rules_enabled,
            rule_excerpts=_rule_excerpts_for_planner(db, module, post_events, game_session),
        )
        plan = await turn_planner.run_turn_planner(get_fast_llm(), plan_messages)
        planned_effects.enforce_plan_item_locations(
            plan, module, game_session.current_scene_id,
        )
        if need_sanity:
            async for chunk in _ensure_planned_sanity(
                db, session_id, game_session, player_char, party_others, plan, pre_gen_seq,
                module=module,
            ):
                room_hub.broadcast(session_id, chunk)
        if need_mishap:
            async for chunk in _ensure_planned_mishap(
                db, session_id, player_char, party_others, plan, pre_gen_seq, module=module,
            ):
                room_hub.broadcast(session_id, chunk)

    # 叙事进度记账：本轮演过的场景机制点 / 摆到玩家面前的线索 → 确定性写世界记忆。
    # 放在 _process_commands 之后，KP 自发的 [HANDOUT]/线索指令已落账，这里只补它漏的。
    planned_effects.record_narrated_progress(
        db, session_id, game_session, module, player_char, party_others, pre_gen_seq,
    )

    # 玩家亲口申请的检定必须有归宿：KP 把它写成叙事顺过去时确定性补挂（详见守卫的文档）。
    if requested_check is not None:
        cmd = planned_effects.requested_check_fallback_command(
            db, session_id, requested_check[0], requested_check[1], pre_gen_seq,
        )
        if cmd:
            async for chunk in _process_commands(
                db, session_id, cmd, module, player_char, game_session, llm,
                teammates=party_others, allow_rule_lookup=False,
                scene_groups=scene_groups if split_mode else None,
                focus_group_label=focus_label,
                pre_gen_seq=pre_gen_seq,
            ):
                room_hub.broadcast(session_id, chunk)

    if then_team_turn:
        db.refresh(game_session)  # 叙事里可能有 [SCENE_CHANGE]/[MOVE] 改了位置，重取再判分头
        async for chunk in _run_team_turn(
            db, session_id, game_session, module, player_char, then_team_turn, get_llm(),
        ):
            room_hub.broadcast(session_id, chunk)

    await _finish_generation(db, session_id, llm)


async def run_check_request_generation(
    session_id: str, actor_id: str, skill: str, intent: str = "",
) -> None:
    """玩家『申请』检定：交 KP 裁定本次是否需要检定、用什么难度（玩家不指定难度）。

    ``intent`` 是玩家顺带说明的检定目标（如「查书桌暗格」）——现场同时有多条线索/多个
    可疑点时，光报技能名 KP 猜不出具体针对什么，必须带上这句话才能裁定到位。
    KP 若判定需要，会输出 [DICE_CHECK]，经 _process_commands 挂成「待玩家投骰」；
    若判定无需检定，则直接简短叙述。"""
    await _drain_housekeeping(session_id)
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
    except Exception:
        logger.exception("检定申请生成失败: session=%s", session_id)
        room_hub.broadcast(session_id, _make_chunk("system", "生成出错，请重试"))
        room_hub.broadcast(session_id, _make_chunk("done"))
    finally:
        db.close()


async def run_combat_aftermath_generation(session_id: str) -> None:
    """战斗/追逐结束后**主动**生成余波叙述——无需玩家先开口。

    复用既有「combat_result 折回主 KP」通道：build_kp_context 会把结果摘要注入本轮上下文，
    KP 承接直接后果、交代在场者状态、把主动权交还调查员。读一次即清 combat_result，
    避免玩家下一次行动时 _run_generation 再注入一遍余波。无结果摘要 / 无 LLM 则安静收场。
    """
    await _drain_housekeeping(session_id)
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
            COMBAT_AFTERMATH_PROMPT,
        )
        # 读一次即清：_run_kp_turn 走 build_kp_context 注入了 combat_result 但不清除
        # （只有 _run_generation 会清），这里补清，避免下一次玩家回合重复注入余波。
        db.refresh(game_session)
        if (game_session.world_state or {}).get("combat_result"):
            ws = dict(game_session.world_state)
            ws.pop("combat_result", None)
            game_session.world_state = ws
            db.commit()
    except asyncio.CancelledError:
        logger.info("战斗余波生成被取消: session=%s", session_id)
    except Exception:
        logger.exception("战斗余波生成失败: session=%s", session_id)
        room_hub.broadcast(session_id, _make_chunk("done"))
    finally:
        db.close()


async def run_roll_generation(session_id: str, check_id: str) -> None:
    """玩家点『投骰』：取出待定检定 → 按 KP 定的难度掷骰 → 广播达成等级 → KP 据等级续写。

    **顺序要紧**：掷骰是纯确定性引擎调用，一次 LLM 都不需要，所以先把点数掷出来广播给玩家，
    再去等上一轮的后台收尾（滚动摘要 / 幕后推演，都是 LLM 调用）。
    以前是开头就 `await _drain_housekeeping`，玩家点了投骰却先看到「KP 正在整理笔记」、
    要等两次 LLM 才出骰子动画——点得越快等得越久。
    """
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

            source = str(check.get("source") or "")
            san_chunks, san_desc = turn_effects._settle_san_target(
                db,
                session_id,
                game_session,
                target_char,
                str(check.get("success_loss") or "0"),
                str(check.get("failure_loss") or "1d6"),
                source,
                mark_checked=False,
            )
            for chunk in san_chunks:
                room_hub.broadcast(session_id, chunk)

            # 与普通待投检定相同：骰子先广播，再等上一轮 housekeeping，最后才写 world_state。
            await _drain_housekeeping(session_id)
            db.expire_all()
            session_service.pop_pending_check(db, session_id, check_id)
            game_session = db.get(GameSession, session_id)
            turn_effects._mark_san_checked(db, game_session, source, char_id)

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

        skill = display_skill_name(check["skill"])
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
        # 临时疯狂：症状波及该技能域 → 自动加惩罚骰（确定性，不问 KP 当初有没有标 penalty）。
        from app.rules.coc import madness as coc_madness
        madness_state = (char_data.get("system_data") or {}).get("madness")
        madness_penalty = coc_madness.check_penalty(madness_state, skill)
        if madness_penalty:
            penalty += madness_penalty
            label = str((madness_state or {}).get("label") or "").strip()
            modifiers.append({
                "kind": "penalty", "n": madness_penalty,
                "reason": f"临时疯狂{f'·{label}' if label else ''}影响了这项技能",
            })
        engine = get_engine(module.rule_system)
        result = engine.resolve_check(
            char_data, skill, difficulty, bonus=bonus, penalty=penalty,
            options=rule_options_service.effective(db, game_session),
        )
        tier_cn = TIER_LABEL.get(result.tier, result.tier)

        dice_content = (
            f"{shown_name}｜{skill} 检定（{difficulty}）：{tier_cn}（{result.description}）"
        )
        dice_meta = {
            "skill": skill, "skill_value": result.skill_value, "roll": result.roll,
            "target": result.target, "outcome": result.outcome, "tier": result.tier,
            "actor": shown_name, "dice": _check_dice_detail(result, modifiers),
        }
        ev = session_service.add_event(
            db, session_id, "dice", dice_content, actor_name="系统", metadata=dice_meta,
        )
        room_hub.broadcast(
            session_id,
            _make_chunk("dice", dice_content, metadata=dice_meta, event_id=ev.id),
        )

        # 骰子已经落地、动画已经在玩家那边跑起来了，现在才等上一轮后台收尾。
        # 但玩家看到的是「骰子出了，然后 KP 迟迟不说话」——这段等待此前一处埋点都没有，
        # 只能靠体感描述。骰子落地到 KP 开口之间的每一段都要能计时。
        t_roll = time.monotonic()
        t_drain = time.monotonic()
        await _drain_housekeeping(session_id)
        drain_s = time.monotonic() - t_drain
        if drain_s > 0.5:
            logger.info("耗时|投骰后等上轮收尾 %.1fs session=%s", drain_s, session_id)
        # housekeeping 是在另一个 Session 里提交的；本会话的身份映射还挂着旧的
        # world_state，直接写回会把它刚写的摘要/记忆盖掉。expire 掉强制重新取。
        db.expire_all()
        # 到这里才真正消费掉待定检定（唯一的 world_state 写入，已在 drain 之后）。
        session_service.pop_pending_check(db, session_id, check_id)
        game_session = db.get(GameSession, session_id)

        # 幸运消费（家规开启时）：这一骰差几点够得着？够得着就**停在这里**问玩家买不买。
        # 必须停：后面的物品发货、线索记账、KP 续写都以成败为输入，一旦跑起来就回不了头了。
        offer = coc_luck.rescue_offer(
            result, difficulty, char_data,
            coc_options.from_dict(rule_options_service.effective(db, game_session)),
            in_combat=bool(combat_service.get_combat(game_session)),
        )
        if offer and char_id:
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
            ))
            room_hub.broadcast(session_id, _make_chunk("done"))
            return

        await _settle_rolled_check(
            db, session_id, game_session, module, player_char, party_others,
            check=check, result=result, skill=skill, difficulty=difficulty,
            source=source, disp_name=disp_name, t_roll=t_roll,
        )
    except asyncio.CancelledError:
        logger.info("投骰生成被取消: session=%s", session_id)
        room_hub.broadcast(session_id, _make_chunk("done"))
    except Exception:
        logger.exception("投骰生成失败: session=%s", session_id)
        room_hub.broadcast(session_id, _make_chunk("done"))
    finally:
        db.close()


async def run_luck_decision(session_id: str, spend: bool) -> None:
    """玩家对「要不要花幸运」拍板后，接着把结算链走完。

    ``spend=True`` 才扣点改判；``False`` 是「认了这次失败」。无论哪种，之后都汇回
    ``_settle_rolled_check`` 那一条链——买来的成功和掷出来的成功，后续待遇必须一模一样。
    """
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

        if spend:
            char = db.get(Character, str(pending.get("char_id") or ""))
            options = coc_options.from_dict(
                rule_options_service.effective(db, game_session),
            )
            char_data = {
                "base_attributes": char.base_attributes if char else {},
                "skills": char.skills if char else {},
                "system_data": char.system_data if char else {},
            }
            cost = int(offer.get("cost") or 0)
            # 重新校验而不是照单全收：这份 offer 是上一步存下来的，中间幸运值可能已被
            # 别处扣掉（同一轮的另一次消费、KP 手动改卡）。
            if char is not None and cost > 0 and coc_luck.available_luck(char_data) >= cost:
                deduction = coc_luck.deduct(char_data, cost)
                if deduction["path"].startswith("system_data."):
                    turn_effects._update_character_stat(
                        db, char, deduction["path"].split(".", 1)[1], deduction["new"],
                    )
                else:
                    _set_attr_luck(db, char, deduction["new"])
                result = coc_luck.apply_rescue(result, difficulty, cost, options)
                spent = cost
                _broadcast_luck_applied(
                    db, session_id, pending, result, cost, deduction,
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
            t_roll=time.monotonic(), luck_spent=spent,
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
) -> None:
    """把「花了几点、原本掷了多少、现在算什么」摆到台面上，并订正那张结果卡。

    改判必须回到原来那张 dice 事件上：玩家（和重连的人）看的是历史记录，留着一张
    写着「失败」的卡、旁边另起一条「其实成功了」，账就对不上了。
    """
    tier_cn = TIER_LABEL.get(result.tier, result.tier)
    shown_name = str(pending.get("shown_name") or "")
    skill = str(pending.get("skill") or "")
    original = dict(pending.get("result") or {})
    event_id = str(pending.get("dice_event_id") or "")
    content = (
        f"{shown_name}｜{skill} 检定：花 {cost} 点幸运（{deduction['old']}→{deduction['new']}），"
        f"{original.get('roll')} → {result.roll}，改判 {tier_cn}"
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
    }


def _check_result_from_payload(payload: dict):
    """``_check_result_payload`` 的逆操作。"""
    from app.rules.base import CheckResult

    return CheckResult(**{
        key: payload.get(key)
        for key in (
            "skill_name", "skill_value", "roll", "target", "outcome", "description",
            "tier", "meets_difficulty", "tens", "tens_kept", "units", "bonus", "penalty",
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
    t_roll: float,
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
    logger.info(
        "耗时|投骰合计 %.1fs（%s）session=%s",
        time.monotonic() - t_roll, usage_tracker.fmt(usage_tracker.snapshot()), session_id,
    )
    usage_tracker.warn_if_reasoning_dominates(usage_tracker.snapshot())


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
    await _drain_housekeeping(session_id)
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
    await _drain_housekeeping(session_id)
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
        res = ["", "", [], [], []]
        kp = KPAgent(get_llm())
        try:
            async for chunk in _stream_narration_filtered(
                kp, messages, res, npcs=_matcher_npcs(module, party_others, game_session),
            ):
                room_hub.broadcast(session_id, chunk)
        except asyncio.CancelledError:
            _persist_narration(db, session_id, res)
            raise
        _persist_narration(db, session_id, res)
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
    await _drain_housekeeping(session_id)
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
        # 确定性切换该角色位置（主角则一并更新 current_scene_id），并落一条「前往」行动
        session_service.set_char_location(db, session_id, actor.id, scene_id)
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
    await _drain_housekeeping(session_id)
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
        await _run_generation(
            db, session_id, game_session, module, player_char, events,
            teammates=party_others,
        )
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
_process_commands = kp_tool_loop._process_commands
_handle_rule_lookup = kp_tool_loop._handle_rule_lookup
_handle_module_lookup = kp_tool_loop._handle_module_lookup
