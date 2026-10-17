"""AI 队友回合决策、行动落库与暗骰处理。"""

from __future__ import annotations

import asyncio
import json
import logging
from difflib import SequenceMatcher
import re
import time
from collections.abc import AsyncIterator

from sqlalchemy.orm import Session

from app.ai.agents.kp_agent import KPAgent
from app.ai.agents.team_agent import TeamAgent
from app.ai.context import build_team_context
from app.models.character import Character
from app.models.module import Module
from app.models.session import GameSession
from app.services import (
    agent_runtime,
    intent as team_intent,
    intent_ledger,
    agent_trace,
    dice_runtime,
    narration_protocol,
    rule_options_service,
    session_service,
    turn_context,
    world_memory,
)
from app.services.event_protocol import make_chunk

_make_chunk = make_chunk
_filter_narration_stream = narration_protocol.filter_narration_stream
_resolve_scene_ref = turn_context._resolve_scene_ref
_scene_name = turn_context._scene_name
_apply_world_memory = turn_context._apply_world_memory
_match_single_npc = turn_context._match_single_npc
_check_dice_detail = dice_runtime._check_dice_detail
ALWAYS_BLIND_SKILLS = dice_runtime.ALWAYS_BLIND_SKILLS
TIER_LABEL = dice_runtime.TIER_LABEL

logger = logging.getLogger(__name__)

def _matcher_npcs(
    module: Module,
    teammates: list[Character] | None,
    session: GameSession | None = None,
) -> list[dict]:
    """供行内台词归属用的名字表：模组 NPC + 已转正/已登记的临场 NPC + 在场队友（真人/AI）。

    队友不在 module.npcs 里，若不加进来，KP 偶尔替队友写的引号台词会被
    错误归给附近提到的某个模组 NPC（如把约翰·卡特的话记到萨沙·卡纳头上）。
    已转正的临场 NPC 同理并入。**已登记但未转正的临场龙套（管理员/护士长…）也并入**：
    否则它们的台词无名可归，会被在名字表里的某个模组 NPC（甚至已死反派）劫走——即
    「管理员的话被记到沃尔特·科比特头上」。只并入通过合理性校验的名字（滤掉旁白碎片）。
    """
    extra = [{"name": t.name, "is_player": True} for t in (teammates or []) if t.name]
    promoted = world_memory.promoted_npc_cards(session.world_state or {}) if session else []
    improv: list[dict] = []
    if session:
        promoted_names = {c.get("name") for c in promoted}
        for name in (session.world_state or {}).get("improvised_npcs") or {}:
            name = str(name).strip()
            if (name and name not in promoted_names
                    and world_memory.is_plausible_npc_name(name)):
                improv.append({"name": name})
    return (module.npcs or []) + promoted + improv + extra




#: 单个队友的决策墙钟上限。超时者按 silent 处理，绝不让一个慢请求拖住整轮。
TEAM_AGENT_TIMEOUT_SECONDS = 20.0


async def _stream_narration_filtered(
    kp: KPAgent, messages: list[dict], result: list,
    npcs: list[dict] | None = None,
    group_label: str | None = None,
    party_names: set[str] | None = None,
    shown_dialogues: list[str] | None = None,
    prior_narration: str = "",
    masker=None,
    npc_guard=None,
) -> AsyncIterator[str]:
    """旧路径入口：KPAgent 流式生成 + 台词过滤（核心逻辑在 _filter_narration_stream）。"""
    async for chunk in _filter_narration_stream(
        kp.narrate(messages), result, npcs=npcs, group_label=group_label,
        party_names=party_names, shown_dialogues=shown_dialogues,
        prior_narration=prior_narration, masker=masker, npc_guard=npc_guard,
    ):
        yield chunk



MAX_TEAMMATES_PER_TURN = 4

# check：队友主动发起的技能检定（content 描述尝试、skill 给技能），先落 action 事件再掷骰。
TEAM_ACTION_EVENT = {
    "speak": "dialogue", "act": "action", "assist": "action", "check": "action",
}


def _parse_team_decision(raw) -> dict | None:
    """解析队友 agent 的 JSON 决策；失败返回 None（编排层据此 hold）。"""
    if isinstance(raw, dict):
        data = raw
    elif isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", raw, re.S)
            if not m:
                return None
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
    else:
        return None
    action = str(data.get("action") or "").strip().lower()
    if action not in TEAM_ACTION_EVENT and action not in ("silent", "travel", "stay"):
        return None
    return {
        "action": action,
        "content": str(data.get("content") or "").strip(),
        "skill": str(data.get("skill") or "").strip(),   # 仅 action=check 时有意义
        "target": str(data.get("target") or "").strip(),
        "verb": str(data.get("verb") or "").strip(),
        "depends_on": data.get("depends_on") or [],
    }


_SELF_REF_WORDS = ("他", "她", "该角色")
_SELF_SPEECH_VERBS = (
    "说道", "说", "道", "表示", "问道", "回答", "喊道", "低声道", "低声说",
    "喃喃道", "喃喃说", "冷冷地说", "轻声说",
)


def _normalize_team_content(content: str, character_name: str, action: str) -> str:
    """把明显的「角色名/他/她 + 自己动作」归一为队友第一人称。

    这是输出边界的保守清洗，不试图改写整段中文；只处理开头的自称标签，避免第三人称
    继续进入事件流和 team_memory，随后又被模型当成自己的说话习惯模仿。
    """
    text = (content or "").strip()
    name = (character_name or "").strip()
    if not text or not name:
        return text

    prefix = None
    for candidate in (name, *(_SELF_REF_WORDS)):
        for wrapped in (candidate, f"【{candidate}】", f"[{candidate}]", f"「{candidate}」"):
            if text.startswith(wrapped):
                prefix = wrapped
                break
        if prefix:
            break
    if prefix is None:
        return text

    rest = text[len(prefix):].lstrip()
    if action == "speak":
        # 「林知微说：『……』」/「林知微：『……』」应只保留角色实际说出口的内容。
        verb_pattern = "|".join(re.escape(verb) for verb in sorted(_SELF_SPEECH_VERBS, key=len, reverse=True))
        rest = re.sub(
            rf"^(?:{verb_pattern})\s*[:：,，]?\s*", "", rest,
        )
        rest = rest.lstrip(" :：,，")
        if len(rest) >= 2 and rest[0] in "「『“‘\"" and rest[-1] in "」』”’\"":
            rest = rest[1:-1].strip()
        return rest

    # 非台词内容保留动作，只把开头的第三人称主体换成「我」。
    rest = rest.lstrip(" :：,，")
    return "我" + rest


def _select_relevant_teammates(
    events, game_session, player_char, teammates,
) -> list:
    """队友相关性门控：玩家明确点名时，别让分处两地的未点名队友也跑主模型。

    规则刻意保守：
    - 玩家最近一句没有点名任何队友 → 全部保留（不猜“这轮没他的事”）；
    - 玩家点名了 A → 保留 A、与玩家同场景的队友，以及分头场景里本轮仍有新事件的队友；
    - 一个都留不下（数据异常）→ 回退全体，宁可多跑也不让队伍突然沉默。
    """
    player_events = [
        e for e in events or []
        if getattr(e, "actor_id", None) == player_char.id
        and getattr(e, "event_type", None) in ("action", "dialogue")
        and str(getattr(e, "content", "") or "").strip()
    ]
    if not player_events:
        return list(teammates)
    last = player_events[-1]
    last_text = str(getattr(last, "content", "") or "")
    mentioned = [t for t in teammates if t.name and t.name in last_text]
    if not mentioned:
        return list(teammates)
    player_scene = (
        session_service.get_char_location(game_session, player_char.id)
        or game_session.current_scene_id
    )
    last_seq = getattr(last, "sequence_num", 0) or 0
    selected = []
    for teammate in teammates:
        if teammate in mentioned:
            selected.append(teammate)
            continue
        scene = (
            session_service.get_char_location(game_session, teammate.id)
            or game_session.current_scene_id
        )
        if scene and player_scene and scene == player_scene:
            selected.append(teammate)
            continue
        has_new_scene_event = any(
            (getattr(e, "sequence_num", 0) or 0) > last_seq
            and (getattr(e, "metadata_", None) or {}).get("scene_id") == scene
            for e in events
        )
        if has_new_scene_event:
            selected.append(teammate)
    return selected or list(teammates)


def _team_conflict_key(action: str, content: str) -> tuple[str, str] | None:
    if action not in ("speak", "act", "assist"):
        return None
    text = "".join(str(content or "").split())
    if not text:
        return None
    return action, text


def _is_duplicate_team_action(
    action: str, content: str, seen: list[tuple[str, str]],
) -> bool:
    """并发完成顺序下的重复/冲突检测：同一类动作、语义高度重合的后者让位。

    这不是语义审核，而是确定性的「两个人同时说了同一句话 / 做了同一个动作」兜底。
    阈值刻意高：0.86 以下的不同台词照常保留。
    """
    key = _team_conflict_key(action, content)
    if key is None:
        return False
    act, text = key
    # 短句（「小心」「跟上」）本来就会撞，不算冲突；只拦成段的重复台词/动作。
    if len(text) < 8:
        seen.append(key)
        return False
    for prev_act, prev_text in seen:
        if prev_act != act:
            continue
        if SequenceMatcher(None, prev_text, text).ratio() >= 0.86:
            return True
    seen.append(key)
    return False


async def _run_team_turn(
    db: Session,
    session_id: str,
    game_session: GameSession,
    module: Module,
    player_char: Character,
    teammates: list[Character],
    llm,
    blind_results: list[str] | None = None,
) -> AsyncIterator[str]:
    """玩家输入后的一轮 AI 队友自动响应。

    每个队友只决策一次；决策**并发执行**（同一份事件快照，N 次串行调用变 1 次墙钟时间——
    代价是同轮队友互相看不到彼此这轮刚说的话，撞话题的偶发风险换整体延迟），谁先完成
    谁先提交，不再等最慢的一个。整轮墙钟上限 ``TEAM_AGENT_TIMEOUT_SECONDS``，超时者按
    silent 处理。本函数只由 ``run_chat_generation`` / ``run_travel_generation`` 调用，
    不会自触发，故不存在递归链式生成。

    分头判定：队友所在场景 ≠ 主队锚点场景（主角所在）即视为「分头独处」，据此让
    ``build_team_context`` 下达「主动推进本场景」指引；同处一地仍是克制补位。

    ``blind_results``：队友做「始终暗投」技能（如心理学）检定时，真实成败只 append 到这里、
    由调用方注入当轮 KP 上下文，绝不落库/广播——否则玩家能从事件或网络看到结果而元游戏。
    """
    roster = teammates[:MAX_TEAMMATES_PER_TURN]
    if not roster:
        return
    yield _make_chunk("housekeeping", "队友们正在思考…")
    anchor_scene = (
        session_service.get_char_location(game_session, player_char.id)
        or game_session.current_scene_id
    )
    events = session_service.get_session_events(db, session_id)
    snapshot_seq = max((getattr(e, "sequence_num", 0) or 0) for e in events) if events else 0
    snapshot_world = dict(game_session.world_state or {})
    relevant = _select_relevant_teammates(events, game_session, player_char, roster)
    if len(relevant) < len(roster):
        logger.info(
            "队友相关性门控：本轮只跑 %d/%d 人 session=%s",
            len(relevant), len(roster), session_id,
        )
    roster = relevant[:MAX_TEAMMATES_PER_TURN]
    if not roster:
        return

    async def _decide(teammate: Character) -> str | None:
        tm_scene = (
            session_service.get_char_location(game_session, teammate.id)
            or game_session.current_scene_id
        )
        separated = bool(tm_scene and anchor_scene and tm_scene != anchor_scene)
        messages = build_team_context(
            teammate, game_session, module, events, player_char,
            all_teammates=teammates, separated=separated,
        )
        bundle = agent_runtime.ContextBundle.from_messages(
            messages, budget_tokens=12000, visibility=(teammate.id,),
        ).fit()
        trace = agent_trace.start(session_id, "team", db=db, char_id=teammate.id)
        t0 = time.monotonic()
        try:
            raw = await TeamAgent(llm, teammate.id).decide(bundle.for_model())
        except asyncio.CancelledError:
            agent_trace.finish(trace, outcome="cancelled", provider=llm)
            raise
        except Exception:
            agent_trace.finish(trace, outcome="error", provider=llm)
            logger.exception("队友决策失败: char=%s", teammate.id)
            return None
        logger.info(
            "耗时|队友决策 %.1fs char=%s(%s)",
            time.monotonic() - t0, teammate.name, teammate.id,
        )
        agent_trace.finish(trace, provider=llm)
        return raw

    seen_actions: list[tuple[str, str]] = []
    team_intents: list[tuple] = []
    committed_actors: set[str] = set()

    async def _commit(teammate: Character, raw: str | None = None, decision: dict | None = None):
        if decision is None:
            if raw is None:
                return
            decision = _parse_team_decision(raw)
        if not decision:
            return  # 解析失败：hold，不重试不递归
        action = decision["action"]
        content = _normalize_team_content(decision["content"], teammate.name, action)
        decision["content"] = content
        if _is_duplicate_team_action(action, content, seen_actions):
            logger.info(
                "队友并发冲突：%s 的 %s 与已提交动作重合，按 silent 处理 session=%s",
                teammate.name, action, session_id,
            )
            return
        target = str(decision.get("target") or "").strip()
        verb = str(decision.get("verb") or "").strip().lower()
        if target and verb:
            current_ws = game_session.world_state or {}
            if not intent_ledger.allows(current_ws, target, verb):
                logger.info(
                    "队友意图对象状态不满足：%s 对 %s 执行 %s，丢弃 session=%s",
                    teammate.name, target, verb, session_id,
                )
                return
        # 队友「前往」：显式移动，确定性切换其所在场景（仅限已知地点），落一条「前往」事件。
        # 队友的移动由此动作触发，KP 不再从其台词臆测搬人；分头分组据 party_locations 归并。
        if action == "travel":
            sid = _resolve_scene_ref(module, decision.get("target") or content)
            known = session_service.known_scene_ids(module, game_session, events)
            cur = session_service.get_char_location(game_session, teammate.id)
            # 连通校验与玩家 travel 同规则：途经必须是去过的场景，不连通/绕不过去都不搬
            # （模组没建图时恒可达）
            if (
                sid and sid in known and sid != cur
                and session_service.find_scene_path(
                    module, cur, sid,
                    via_allowed=session_service.passable_scene_ids(game_session),
                ) is not None
            ):
                session_service.set_char_location(db, session_id, teammate.id, sid)
                db.refresh(game_session)
                label = _scene_name(module, sid)
                ev = session_service.add_event(
                    db, session_id, "action", f"（前往：{label}）",
                    actor_id=teammate.id, actor_name=teammate.name,
                )
                yield _make_chunk(
                    "action", f"（前往：{label}）", actor_name=teammate.name,
                    event_id=ev.id, actor_id=teammate.id,
                )
                # 私有记忆钩子：自己的移动落 deeds（确定性、零 LLM）
                _apply_world_memory(
                    db, game_session,
                    lambda ws, _tid=teammate.id, _q=ev.sequence_num, _s=f"前往了{label}":
                        world_memory.record_team_deed(ws, _tid, _q, _s),
                )
            return
        # 队友「留守」：队伍要走而他不走。位置默认跟随（无记录者由 get_char_location 回落到
        # 当前场景），所以留下必须显式说出来——把当前所在写成他自己的记录，并给事件盖上
        # stay 标记，主角随后移动时据此跳过他（session_service.stayed_char_ids）。
        # 这也是分头行动唯一能被真正触发的入口之一：他留在原地，队伍走到别处，归并才会出现两组。
        if action == "stay":
            here = session_service.get_char_location(game_session, teammate.id)
            if here:
                session_service.set_char_location(db, session_id, teammate.id, here)
                db.refresh(game_session)
            label = _scene_name(module, here) if here else ""
            text = content or f"（留在{label}）"
            ev = session_service.add_event(
                db, session_id, "action", text,
                actor_id=teammate.id, actor_name=teammate.name,
                metadata={session_service.STAY_META_KEY: True, "scene_id": here},
            )
            yield _make_chunk(
                "action", text, actor_name=teammate.name,
                event_id=ev.id, actor_id=teammate.id,
            )
            _apply_world_memory(
                db, game_session,
                lambda ws, _tid=teammate.id, _q=ev.sequence_num, _s=f"留在了{label}":
                    world_memory.record_team_deed(ws, _tid, _q, _s),
            )
            return
        if action == "silent" or not content:
            return
        event_type = TEAM_ACTION_EVENT[action]
        ev = session_service.add_event(
            db, session_id, event_type, content,
            actor_id=teammate.id, actor_name=teammate.name,
        )
        if target and verb:
            _apply_world_memory(
                db, game_session,
                lambda ws: intent_ledger.record(
                    ws, target, teammate.id, verb, ev.sequence_num,
                ),
            )
        # speak 用 npc_dialogue 走前端气泡渲染；act/assist/check 走通用 action 渲染
        chunk_type = "npc_dialogue" if event_type == "dialogue" else "action"
        yield _make_chunk(
            chunk_type, content, actor_name=teammate.name,
            event_id=ev.id, actor_id=teammate.id,
        )
        # 私有记忆钩子：自己的言行落 deeds（确定性、零 LLM）。check 在掷骰后带结果另记。
        if action != "check":
            verb = "说" if event_type == "dialogue" else "做"
            _apply_world_memory(
                db, game_session,
                lambda ws, _tid=teammate.id, _q=ev.sequence_num, _s=f"{verb}：{content}":
                    world_memory.record_team_deed(ws, _tid, _q, _s),
            )
        # 队友主动检定：紧接着掷骰，结果落库交由 KP 收束叙述。心理学等「始终暗投」技能只落
        # 「做了一次暗骰」的事实、结果仅回灌 KP（经 blind_results 注入当轮上下文），绝不落库/
        # 广播成败——否则玩家能从事件或网络看到结果而元游戏。
        if action == "check" and decision.get("skill"):
            skill = decision["skill"]
            cdata = {
                "base_attributes": teammate.base_attributes,
                "skills": teammate.skills,
                "system_data": teammate.system_data,
            }
            result = rule_options_service.ruleset_by_id(db, session_id).check.resolve_skill_check(
                cdata, skill, "normal",
            )
            if any(s in skill for s in ALWAYS_BLIND_SKILLS):
                tier_cn = TIER_LABEL.get(result.tier, result.tier)
                dice_content = f"{teammate.name} 进行了一次暗骰·{skill}（结果仅 KP 可见）"
                dice_meta = {"skill": skill, "actor": teammate.name, "blind": True}
                if blind_results is not None:
                    blind_results.append(
                        f"【暗骰·{teammate.name}·{skill}（结果仅你 KP 可见，绝不可把成败直接告诉玩家）】"
                        f"达成 {tier_cn}：{result.description}"
                    )
            else:
                dice_content = f"{teammate.name}｜{skill} 检定（normal）：{result.description}"
                dice_meta = {
                    "skill": skill, "skill_value": result.skill_value,
                    "roll": result.roll, "target": result.target,
                    "outcome": result.outcome, "actor": teammate.name,
                    "dice": _check_dice_detail(result),
                }
            dev = session_service.add_event(
                db, session_id, "dice", dice_content,
                actor_name="系统", metadata=dice_meta,
            )
            yield _make_chunk("dice", dice_content, metadata=dice_meta, event_id=dev.id)
            # 私有记忆钩子：检定连同结果落 deeds。暗骰不落成败——team_memory 会注入队友
            # 自身上下文，落了成败就可能经其言行外泄（与 blind_results 的守密边界一致）。
            deed = (
                f"做：{content}（暗骰·{skill}）"
                if dice_meta.get("blind")
                else f"做：{content}（{skill}检定：{TIER_LABEL.get(result.tier, result.tier)}）"
            )
            _apply_world_memory(
                db, game_session,
                lambda ws, _tid=teammate.id, _q=dev.sequence_num, _s=deed:
                    world_memory.record_team_deed(ws, _tid, _q, _s),
            )
            # 世界记忆钩子 d：队友暗投若能确定性归属到唯一 NPC（行动描述里恰好点名一个），
            # 记录该 NPC「被看穿/未被看穿」；归属不成立则跳过，绝不猜测。
            if dice_meta.get("blind"):
                target = _match_single_npc(module, content)
                if target:
                    seen_through = result.outcome in (
                        "critical_success", "hard_success", "success",
                    )
                    verdict = "看穿" if seen_through else "试探，但未被看穿"
                    _apply_world_memory(
                        db, game_session,
                        lambda ws: world_memory.record_npc_interaction(
                            ws, target[0], dev.sequence_num,
                            f"被 {teammate.name} 用{skill}{verdict}",
                        ),
                    )

    async def _decide_pair(teammate: Character):
        return teammate, await _decide(teammate)

    owners = {
        asyncio.create_task(_decide_pair(t)): t
        for t in roster
    }
    if owners:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + TEAM_AGENT_TIMEOUT_SECONDS
        pending = set(owners)
        while pending:
            remaining = max(0.0, deadline - loop.time())
            done, still = await asyncio.wait(
                pending, timeout=remaining, return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                for task in still:
                    task.cancel()
                logger.warning(
                    "队友回合超时 %.0fs，未完成的队友按 silent 处理: session=%s",
                    TEAM_AGENT_TIMEOUT_SECONDS, session_id,
                )
                break
            pending = still
            for task in done:
                teammate = owners[task]
                try:
                    _teammate, raw = task.result()
                except asyncio.CancelledError:
                    continue
                except Exception:
                    logger.exception("队友决策失败: char=%s", teammate.id)
                    continue
                if raw is None:
                    continue
                decision = _parse_team_decision(raw)
                if not decision:
                    continue
                action = decision.get("action")
                # 台词/沉默保持完成即提交；会改世界的动作先攒着，等本轮决策齐了做确定性冲突裁决。
                if action in ("speak", "silent"):
                    async for chunk in _commit(teammate, decision=decision):
                        yield chunk
                    committed_actors.add(teammate.id)
                    committed_actors.add(teammate.name)
                else:
                    decision["observes_seq"] = snapshot_seq
                    parsed_intent = team_intent.from_decision(
                        teammate, decision, snapshot_seq, world_state=snapshot_world,
                    )
                    decision["_target_version"] = parsed_intent.target_version
                    team_intents.append((teammate, decision, parsed_intent))

    resolved = team_intent.resolve_team_intents(team_intents, committed_actors, world_state=snapshot_world)
    if len(resolved) < len(team_intents):
        logger.info(
            "队友意图冲突：%d 条同目标动作被裁决丢弃 session=%s",
            len(team_intents) - len(resolved), session_id,
        )
    for teammate, decision, _intent in resolved:
        async for chunk in _commit(teammate, decision=decision):
            yield chunk
