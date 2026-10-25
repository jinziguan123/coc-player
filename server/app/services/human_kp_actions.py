"""真人 KP 工具桌到确定性领域动作的适配。"""

from __future__ import annotations

import re
import uuid

from sqlalchemy.orm import Session

from app.models.character import Character
from app.models.module import Module
from app.models.session import GameSession
from app.services import (
    chase_service,
    combat_service,
    dice_runtime,
    human_kp_desk,
    human_kp_service,
    illustration_service,
    kp_actions,
    movement,
    npc_identity,
    rule_options_service,
    scene_kind,
    session_service,
    turn_context,
    turn_effects,
)
from app.services.event_protocol import event_to_chunk, make_chunk as _make_chunk, split_ooc

_resolve_opposed = dice_runtime._resolve_opposed
_exec_generic_roll = dice_runtime._exec_generic_roll
_attach_npc_portrait = illustration_service._attach_npc_portrait
_scene_name = turn_context._scene_name
_exec_dice_check = turn_effects._exec_dice_check
_exec_scene_change = turn_effects._exec_scene_change
_exec_flag = turn_effects._exec_flag
_exec_handout = turn_effects._exec_handout
_exec_hp_change = turn_effects._exec_hp_change
_exec_san_check = turn_effects._exec_san_check
_exec_start_combat = kp_actions._exec_start_combat
_exec_npc_act = kp_actions._exec_npc_act
_exec_mark_seen = turn_effects._exec_mark_seen


async def execute_human_kp_action(
    db: Session,
    session_id: str,
    game_session: GameSession,
    module: Module,
    action: str,
    payload: dict,
) -> tuple[list[str], str]:
    """真人 KP M1 工具桌：把表单动作路由到既有确定性执行器。

    这里不生成 AI 叙事，也不复制骰子、场景、战斗和库存规则；只负责把真人 KP 的
    触发转换为现有执行器调用，并返回待广播的 chunks 与简短结果。
    """
    player_char = human_kp_service.resolve_player_character(
        db, session_id, game_session,
    )
    if player_char is None:
        raise ValueError("会话缺少主角角色，无法执行 KP 动作")
    teammates = session_service.get_party_members(
        db, session_id, exclude_id=player_char.id,
    )
    action = str(action or "").strip()
    payload = payload if isinstance(payload, dict) else {}

    if action == "narration":
        content = str(payload.get("content") or "").strip()
        if not content:
            raise ValueError("叙事内容不能为空")
        # 小括号里的话不算旁白，与玩家侧同一个约定（见 event_protocol.split_ooc）。
        # KP 不占玩家席、没有发言框，此前想跟同桌说句「稍等，我翻一下本子」都没地方说，
        # 只能混在旁白里发出去——那会被当成故事的一部分，还会进 KP 的上下文。
        body, aside = split_ooc(content)
        chunks = []
        if body:
            chunks.append(event_to_chunk(session_service.add_event(
                db, session_id, "narration", body, actor_name="KP",
                metadata={"kp_manual": True},
            )))
        if aside:
            chunks.append(event_to_chunk(session_service.add_event(
                db, session_id, "ooc", aside, actor_name="KP",
            )))
        if not chunks:
            raise ValueError("叙事内容不能为空")
        if not body:
            return chunks, "场外发言已发出"
        return chunks, "叙事已发布" if not aside else "叙事已发布，场外话另发了一条"

    if action == "dialogue":
        ref = str(payload.get("npc_id") or payload.get("actor_name") or "").strip()
        content = str(payload.get("content") or "").strip()
        if not ref or not content:
            raise ValueError("NPC 与台词内容不能为空")
        npc = next(
            (
                n for n in (module.npcs or [])
                if str(n.get("id") or "") == ref or str(n.get("name") or "") == ref
            ),
            None,
        )
        actor_id = str(npc.get("id")) if npc and npc.get("id") else None
        true_name = str(npc.get("name") or ref) if npc else ref
        # 台词里的小括号同样不算台词——「（他其实在说谎）」这种给同桌看的注解，
        # 不该被 NPC 说出口，也不该进上下文当成他真讲过的话。
        line, aside = split_ooc(content)
        if not line:
            raise ValueError("台词内容不能为空——整句都在小括号里的话，用叙事框发场外话")
        # 气泡上的说话人是玩家可见面，与 AI KP 的 say 同一口径（kp_actions）：玩家还没认出
        # 这个 NPC 时报对外称呼；这句台词自报家门的那一刻起改口。KP 不在模组里的名字原样用。
        actor_name = (
            npc_identity.build_masker(db, session_id, module)(true_name, extra_prose=line)
            if npc else true_name
        )
        ev = session_service.add_event(
            db, session_id, "dialogue", line,
            actor_id=actor_id, actor_name=actor_name,
            metadata={"kp_manual": True},
        )
        if npc:
            _attach_npc_portrait(db, session_id, module, ev)
        metadata = {"portrait": (ev.metadata_ or {}).get("portrait")} if (ev.metadata_ or {}).get("portrait") else None
        chunks = [
            _make_chunk(
                "dialogue", line, actor_name=actor_name, actor_id=actor_id,
                event_id=ev.id, metadata=metadata,
            )
        ]
        if aside:
            chunks.append(event_to_chunk(session_service.add_event(
                db, session_id, "ooc", aside, actor_name="KP",
            )))
        receipt = "NPC 台词已发布" if not aside else "台词已发布，场外话另发了一条"
        if actor_name != true_name:
            receipt += f"（玩家还没认出{true_name}，气泡上显示为「{actor_name}」）"
        return chunks, receipt

    if action == "dice_check":
        kv = {str(k): str(v) for k, v in payload.items() if v is not None}
        if not kv.get("skill", "").strip():
            raise ValueError("检定技能不能为空")
        chunks, descs, pending = await _exec_dice_check(
            db, session_id, game_session, module, kv, player_char, teammates,
        )
        # 从待办里的玩家申请发起的：发出去就算处理过了
        human_kp_desk.mark_request_handled(db, game_session, kv.get("request_id", "").strip())
        return chunks, "已发起待投检定" if pending else ("；".join(descs) or "检定已结算")

    if action == "opposed_check":
        descs: list[str] = []
        chunks = [
            c async for c in _resolve_opposed(
                db, session_id, payload, module, player_char, teammates, descs,
            )
        ]
        if not descs:
            raise ValueError("对抗检定至少需要双方角色与技能")
        return chunks, "；".join(descs)

    if action == "generic_roll":
        return _exec_generic_roll(db, session_id, module, payload)

    if action == "scene_change":
        # 真人 KP 是裁定者：可点名移动者（movers），不点名就搬与主角同处、本轮没说留守的全员
        # （真人 KP 的切换按钮一向是「整队过去」，不套用 AI 回合里「不替没表态的真人决定」那条）。
        party = [player_char, *(teammates or [])]
        raw = str(payload.get("movers") or "").strip()
        movers, unknown = movement.resolve_tool_movers(raw, party)
        if raw and (unknown or not movers):
            raise ValueError(f"认不出的角色：{'、'.join(unknown) or raw}")
        if not movers:
            here = session_service.get_char_location(game_session, player_char.id)
            stayed = session_service.stayed_char_ids(db, session_id)
            movers = [
                c for c in party
                if c.id == player_char.id or (
                    c.id not in stayed and session_service.get_char_location(game_session, c.id) == here
                )
            ]
        chunks, sid, note = await _exec_scene_change(
            db, session_id, game_session, module,
            str(payload.get("scene_id") or payload.get("scene") or "").strip(),
            player_char, teammates, movers=movers,
        )
        return chunks, note or (f"已切换至 {_scene_name(module, sid)}" if sid else "场景未变化")

    if action in ("set_flag", "clear_flag"):
        flag = str(payload.get("flag") or "").strip()
        if not flag:
            raise ValueError("剧情标志不能为空")
        chunks = _exec_flag(db, session_id, game_session, flag, action == "set_flag")
        # 标志名只写进 KP 自己的回执，不对玩家播报（见 _exec_flag）
        return chunks, f"剧情标志已{'置上' if action == 'set_flag' else '清除'}：{flag}（玩家不可见）"

    if action == "set_mood":
        chunks, note = turn_effects.exec_set_mood(
            db, session_id, game_session, str(payload.get("mood") or ""),
        )
        if not chunks:
            raise ValueError(note)
        return chunks, note.removeprefix("ok：")

    if action == "handout":
        hid = str(payload.get("id") or payload.get("handout_id") or "").strip()
        if not hid:
            raise ValueError("手书 ID 不能为空")
        return await _exec_handout(
            db, session_id, game_session, module, hid, player_char, teammates,
        )

    if action == "hp_change":
        return await _exec_kp_hp_change(
            db, session_id, game_session, module, player_char, teammates, payload,
        )

    if action == "san_check":
        if not str(payload.get("source") or "").strip():
            # 无名 SAN 每次一把新键：真人 KP 不会「重新生成」，同一轮发两次就是两次恐怖
            payload = {**payload, "san_key": f"kp:{uuid.uuid4().hex[:12]}"}
        chunks, descs, pending = await _exec_san_check(
            db, session_id, game_session, payload, player_char, teammates,
        )
        if pending:
            return chunks, "已向真人目睹者发出理智检定请求"
        return chunks, "；".join(descs) if descs else "本次理智检定无需重复结算"

    if action == "start_combat":
        enemies = str(payload.get("enemies") or "").strip()
        if not enemies:
            raise ValueError("至少指定一个敌人")
        participants = [
            ref.removeprefix("character:").removeprefix("char:")
            for ref in _csv(payload.get("participants"))
        ]
        chunks = await _exec_start_combat(
            db, session_id, game_session, module, player_char, teammates,
            None, enemies, str(payload.get("trigger") or "真人 KP 发起战斗"),
            participant_ids=participants or None,
        )
        return chunks, "已切入结构化战斗"

    if action == "end_combat":
        # 战斗的机械收场（击溃/全灭）由结算自动判定；这一条给 KP 主观收尾：
        # 敌人逃了、谈和了、或这一架不值得再打下去——结果摘要落 world_state.combat_result，
        # 收尾行与 combat_end 广播由 _end_combat 一并产出，前端据 combat_end 退出战术台。
        return combat_service.end_combat(db, session_id, "manual"), "战斗已结束"

    if action == "unlock_place":
        return _exec_unlock_place(
            db, session_id, game_session, module,
            str(payload.get("scene_id") or payload.get("scene") or "").strip(),
        )

    if action == "move_actor":
        return _exec_move_actor(
            db, session_id, game_session, module,
            str(payload.get("character_id") or payload.get("char_id") or "").strip(),
            str(payload.get("scene_id") or payload.get("scene") or "").strip(),
        )

    if action == "set_battle_map":
        return _exec_set_battle_map(
            db, game_session, module,
            str(payload.get("scene_id") or "").strip(),
            str(payload.get("url") or "").strip(),
            str(payload.get("level") or "normal").strip(),
        )

    if action == "set_token":
        return _exec_set_token(
            db, session_id, game_session,
            str(payload.get("combatant_id") or "").strip(),
            str(payload.get("figure") or "").strip(),
        )

    if action == "start_chase":
        return _exec_start_chase(
            db, session_id, game_session, module, [player_char, *teammates], payload,
        )

    if action == "chase_round":
        hazard = payload.get("hazard")
        if isinstance(hazard, dict) and str(hazard.get("skill") or "").strip():
            hazard = {
                "who": "pursuer" if hazard.get("who") == "pursuer" else "quarry",
                "skill": str(hazard["skill"]).strip(),
                "difficulty": hazard.get("difficulty") if hazard.get("difficulty") in ("normal", "hard", "extreme") else "normal",
            }
        else:
            hazard = None
        # 真人 KP 模式不调 AI 叙述（agent=None）：这一轮怎么描写，KP 自己写
        chunks = await chase_service.resolve_chase_round(db, session_id, {"hazard": hazard})
        return chunks, "追逐推进了一轮"

    if action == "end_chase":
        outcome = str(payload.get("outcome") or "manual")
        return chase_service.end_chase(db, session_id, outcome), "追逐已结束"

    if action == "mark_seen":
        return _exec_kp_mark_seen(db, session_id, game_session, module, player_char, teammates, payload)

    if action in ("block_path", "unblock_path"):
        ref = str(payload.get("scene_id") or payload.get("scene") or "").strip()
        sid = turn_context._resolve_scene_ref(module, ref) if ref else None
        if not sid:
            raise ValueError("找不到这个地点")
        blocked = action == "block_path"
        turn_effects.set_path_block(
            db, session_id, game_session, module, sid, str(payload.get("reason") or ""), blocked,
        )
        name = _scene_name(module, sid)
        return [], (f"{name} 已标记为此刻过不去：玩家的「前往」会绕开它" if blocked
                    else f"{name} 已恢复通行")

    if action == "travel_suggest":
        ref = str(payload.get("scene_id") or payload.get("scene") or "").strip()
        if not ref:
            raise ValueError("请选择要建议前往的地点")
        chunks, note = turn_effects.travel_suggest_event(
            db, session_id, game_session, module, ref, str(payload.get("reason") or ""),
        )
        if not chunks:
            raise ValueError(note)
        return chunks, "已给玩家挂出「要不要去」的建议卡，去不去由他们自己点"

    if action == "npc_act":
        ref = str(payload.get("npc_id") or "").strip()
        trigger = str(payload.get("trigger") or "").strip()
        npc = next(
            (n for n in (module.npcs or []) if ref and ref in (str(n.get("id") or ""), str(n.get("name") or ""))),
            None,
        )
        if npc is None or not npc.get("id"):
            raise ValueError("请选择模组里的 NPC（临场 NPC 没有人格档案，用「NPC 台词」手写）")
        if not trigger:
            raise ValueError("写一句触发情境：他为什么此刻开口")
        from app.ai.llm_factory import get_llm
        chunks, line = await _exec_npc_act(
            db, session_id, game_session, module, get_llm(), player_char, str(npc["id"]), trigger,
        )
        return chunks, f"{npc.get('name') or npc['id']}（AI 代演）：{line}"

    if action == "cancel_check":
        return human_kp_desk.cancel_pending_check(
            db, session_id, game_session, module, str(payload.get("check_id") or "").strip(),
        )

    if action == "dismiss_request":
        human_kp_desk.mark_request_handled(db, game_session, str(payload.get("request_id") or "").strip())
        return [], "已从待办里移除这条检定申请"

    if action == "edit_event":
        return human_kp_desk.edit_event(
            db, session_id, str(payload.get("event_id") or "").strip(), str(payload.get("content") or ""),
        )

    if action == "withdraw_event":
        return human_kp_desk.withdraw_event(db, session_id, str(payload.get("event_id") or "").strip())

    if action == "combat_control":
        on = str(payload.get("on") or "").strip().lower() in ("1", "true", "on", "yes")
        state = human_kp_service._private_state(game_session)
        state["control_enemies"] = on
        game_session.kp_state = state
        db.commit()
        chunks = await combat_service.set_kp_enemy_control(db, session_id, on)
        return chunks, "敌方回合改由你下令" if on else "敌方回合交回自动行动"

    if action == "combat_npc_act":
        return await combat_service.kp_command_npc(db, session_id, payload), "敌方行动已结算"

    raise ValueError(f"不支持的 KP 动作：{action}")


_HP_EXPR = re.compile(r"\d+(d\d+([+-]\d+)?)?", re.IGNORECASE)


def _roll_hp_amount(db: Session, session_id: str, text: str) -> tuple[int, str]:
    """把「变化值」解析成带符号的整数：-3、+2、-1d6+1、1d3。骰式经规则内核掷。

    返回 (变化值, 掷骰说明)。负号=伤害，其余=治疗——与 AI KP 的 hp_change 同一口径。
    """
    raw = re.sub(r"\s+", "", str(text or ""))
    sign = -1 if raw.startswith("-") else 1
    body = raw.lstrip("+-")
    if not body or not _HP_EXPR.fullmatch(body):
        raise ValueError("变化值写整数或骰式，如 -3、+2、-1d6+1")
    if body.isdigit():
        amount, note = int(body), ""
    else:
        rolled = rule_options_service.ruleset_by_id(db, session_id).dice.roll(body)
        amount, note = max(0, rolled.total), f"{body.upper()}={rolled.total}"
    if amount == 0:
        raise ValueError("变化值为 0，不需要结算")
    return sign * amount, note


async def _exec_kp_hp_change(
    db: Session, session_id: str, game_session: GameSession, module: Module,
    player_char: Character, teammates: list[Character], payload: dict,
) -> tuple[list[str], str]:
    """真人 KP 结算 HP：目标从下拉里点（character:<id> / combatant:<参战方 id>），不猜名字。

    AI KP 的 hp_change 空目标会落到锚点角色、名字按子串匹配——给模型兜底可以，给真人 KP
    就是误伤：选漏了一项，血扣在了别人身上。所以这里空目标直接报错。
    战斗外系统不追踪 NPC 的 HP，NPC 只能在战斗中作为参战方结算。
    """
    ref = str(payload.get("target") or "").strip()
    if not ref:
        raise ValueError("请选择要结算 HP 的角色")
    delta, note = _roll_hp_amount(db, session_id, str(payload.get("delta") or ""))
    reason = str(payload.get("reason") or "").strip()
    reason = f"{reason}（{note}）" if reason and note else (reason or note)
    party = [player_char, *teammates]
    kind, _, ident = ref.partition(":")
    char = None
    if kind == "combatant":
        state = combat_service.get_combat(game_session)
        if not state:
            raise ValueError("当前没有进行中的战斗")
        target = combat_service._find(state, ident)
        if target is None:
            raise ValueError("这个参战方不在场上")
        if not target.get("char_id"):
            return await _exec_npc_hp_change(db, session_id, state, target, delta, reason)
        char = next((c for c in party if c.id == target["char_id"]), None)
    elif kind == "character":
        char = next((c for c in party if c.id == ident), None)
    else:
        char = next((c for c in party if c.name == ref), None)
    if char is None:
        raise ValueError("找不到这个角色——请从下拉里选")
    chunks = await _exec_hp_change(
        db, session_id, player_char, "", str(delta), reason,
        module=module, teammates=teammates, target=char,
    )
    if not chunks:
        raise ValueError("HP 结算失败")
    return chunks, f"{char.name} HP {'-' if delta < 0 else '+'}{abs(delta)}" + (f"（{note}）" if note else "")


async def _exec_npc_hp_change(
    db: Session, session_id: str, state: dict, target: dict, delta: int, reason: str,
) -> tuple[list[str], str]:
    """战斗中给 NPC 参战方结算 HP。KP 填的是最终值，不再过护甲。"""
    rs = rule_options_service.ruleset_by_id(db, session_id)
    if delta < 0:
        lines = combat_service.apply_damage(
            db, state, target, -delta, reason, ignore_armor=True, session_id=session_id,
        )
    else:
        lines = combat_service.apply_heal(db, state, target, delta)
    chunks = [combat_service._combat_line(db, session_id, line) for line in lines]
    end = rs.combat.check_combat_end(state.get("initiative") or [])
    if end:
        chunks += combat_service._end_combat(db, session_id, state, end)
    elif state.get("awaiting_kp") == target.get("id") and not rs.combat.is_active(target):
        # 正等 KP 下令的那个敌人被打倒了：它的回合作废，驱动往下走
        state["awaiting_kp"] = None
        drive_chunks, _beats = await combat_service.drive_npcs(db, session_id, state, resume=True)
        chunks += drive_chunks
    else:
        combat_service._save_combat(db, session_id, state)
        chunks.append(combat_service._combat_state_chunk(state))
    return chunks, "；".join(lines) or "HP 已结算"


def _exec_kp_mark_seen(
    db: Session, session_id: str, game_session: GameSession, module: Module,
    player_char: Character, teammates: list[Character], payload: dict,
) -> tuple[list[str], str]:
    """把线索 / 场景机制点记进台账。``clues`` 可一次给多条（参谋「候选线索」一键记账）。"""
    raw = payload.get("clues")
    clues = [str(c).strip() for c in raw] if isinstance(raw, list) else []
    if str(payload.get("clue") or "").strip():
        clues.append(str(payload["clue"]).strip())
    level = str(payload.get("level") or "direct").strip() or "direct"
    notes = [
        _exec_mark_seen(db, session_id, game_session, module, {"clue": cid, "level": level},
                        player_char, teammates)
        for cid in dict.fromkeys(c for c in clues if c)
    ]
    event = str(payload.get("event") or "").strip()
    if event:
        notes.append(_exec_mark_seen(db, session_id, game_session, module, {"event": event},
                                     player_char, teammates))
    if not notes:
        raise ValueError("线索与机制点至少填一个")
    if not any("已记" in n for n in notes):
        raise ValueError("".join(notes))
    return [], "".join(notes)


def _exec_start_chase(
    db: Session, session_id: str, game_session: GameSession, module: Module,
    party: list[Character], payload: dict,
) -> tuple[list[str], str]:
    """真人 KP 发起追逐：逃方从在座调查员里多选，追方可多选模组 NPC，也接受临场起名。

    距离轨仍只有一条：多人追逐时由**最慢的逃者**与**最快的追兵**代表全组判定，
    其余人记进 state.quarries / state.pursuers，供追逐台展示名单。
    战斗中或已在追逐时不能再开。
    """
    if chase_service.get_chase(game_session):
        raise ValueError("已经在追逐中了")
    if combat_service.get_combat(game_session):
        raise ValueError("战斗中不能同时开追逐，先结束战斗")

    quarry_refs = _csv(payload.get("quarries")) or _csv(payload.get("quarry"))
    if quarry_refs:
        quarries: list[Character] = []
        for ref in quarry_refs:
            key = ref.removeprefix("character:").removeprefix("char:")
            hit = next((c for c in party if c.id == key or c.name == key), None)
            if hit is None:
                raise ValueError("逃方必须是在座的调查员")
            if hit not in quarries:
                quarries.append(hit)
    else:
        # 不点选＝全队逃跑；与开战表单「不点选＝全员参战」保持同一口径。
        quarries = list(party)
    if not quarries:
        raise ValueError("逃方必须是在座的调查员")

    pursuer_refs = _csv(payload.get("pursuers")) or _csv(payload.get("pursuer"))
    pursuer_refs += _csv(payload.get("pursuer_extra"))
    if not pursuer_refs:
        raise ValueError("请指定追方")
    rule_rs = rule_options_service.ruleset_by_id(db, session_id)
    pursuers: list[dict] = []
    seen: set[str] = set()
    mov_override = _int(payload.get("pursuer_mov"))
    for ref in pursuer_refs:
        key = ref.removeprefix("npc:").removeprefix("character:")
        spec = next(
            (n for n in (module.npcs or []) if key in (n.get("name"), n.get("id"))),
            None,
        )
        pursuer = chase_service._pursuer_from_npc(
            spec or {"name": key, "attributes": {"DEX": 50, "CON": 50, "SIZ": 50},
                     "skills": {"运动": 45}},
            rule_rs,
        )
        name = str(pursuer.get("name") or key)
        if name in seen:
            continue
        if mov_override is not None:
            pursuer["mov"] = max(1, mov_override)
        pursuers.append(pursuer)
        seen.add(name)
    if not pursuers:
        raise ValueError("请指定追方")

    escape_at = _int(payload.get("escape_at"), 5)
    caught_at = _int(payload.get("caught_at"), -3)
    start_gap = _int(payload.get("start_gap"), 0)
    if not caught_at < start_gap < escape_at:
        raise ValueError("起始距离要落在「被追上」与「甩脱」之间")

    skill = str(payload.get("skill") or "运动").strip() or "运动"
    quarry_names = "、".join(c.name for c in quarries)
    pursuer_names = "、".join(str(p.get("name") or "追兵") for p in pursuers)
    trigger = str(payload.get("trigger") or "").strip() or f"{pursuer_names}追了上来——"
    _state, chunks = chase_service.start_chase(
        db, session_id, chase_service._quarry_from_char(quarries[0]), pursuers[0],
        skill=skill, escape_at=escape_at, caught_at=caught_at, trigger=trigger,
        start_gap=start_gap,
        quarries=[chase_service._quarry_from_char(c) for c in quarries],
        pursuers=pursuers,
    )
    return chunks, f"追逐开始：{quarry_names} 逃，{pursuer_names} 追"


def _csv(value) -> list[str]:
    """把逗号 / 顿号分隔的字符串或列表整理成去空列表。"""
    if value is None:
        return []
    raw = value if isinstance(value, (list, tuple)) else re.split(r"[,，、]", str(value))
    return [str(item).strip() for item in raw if str(item).strip()]


def _int(value, default: int | None = None) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _exec_set_battle_map(
    db: Session, game_session: GameSession, module: Module,
    scene_id: str, url: str, level: str,
) -> tuple[list[str], str]:
    """给某个场景配一张战场底图（空 url 即撤掉）。

    不广播事件：底图换没换不是剧情，玩家下次拉 /locations 就拿到了。
    """
    scene = _find_scene(module, scene_id) if scene_id else None
    sid = str(scene.get("id")) if scene else (scene_id or game_session.current_scene_id or "")
    if not sid:
        raise ValueError("没有指定场景")
    if level not in ("faint", "normal", "strong"):
        level = "normal"
    ws = dict(game_session.world_state or {})
    maps = dict(ws.get("battle_maps") or {})
    if url:
        maps[sid] = {"url": url, "level": level}
    else:
        maps.pop(sid, None)
    ws["battle_maps"] = maps
    game_session.world_state = ws
    db.commit()
    return [], "战场底图已更新" if url else "战场底图已撤下"


def _exec_set_token(
    db: Session, session_id: str, game_session: GameSession, combatant_id: str, figure: str,
) -> tuple[list[str], str]:
    """给某个参战方挑一枚战棋轮廓。

    机器猜不出「不明存在」长什么样——`initiative` 上只有名字与阵营，没有任何「这是人
    还是东西」的信息。所以这一下由 KP 点，结果存进战斗态、随战斗广播发给全桌：
    轮廓是桌面状态，不能只有 KP 自己看得见。
    """
    if not combatant_id or not figure:
        raise ValueError("参战方与轮廓都不能为空")
    state = combat_service.get_combat(game_session)
    if not state:
        raise ValueError("当前没有进行中的战斗")
    hit = next(
        (p for p in (state.get("initiative") or []) if str(p.get("id")) == combatant_id),
        None,
    )
    if hit is None:
        raise ValueError("这个参战方不在场上")
    hit["figure"] = figure
    combat_service._save_combat(db, session_id, state)
    return [combat_service._combat_state_chunk(state)], \
        f"{hit.get('name') or combatant_id} 的战棋已更换"


def _scene_label(scene: dict) -> str:
    """场景的显示名。**title 在前**：模组导入器写的是 title，`name` 只是部分模组才有的
    别名——只认 name 的话，玩家看到的会是「地图上多了一处去向：scene_5」这种 id。
    与 navigation_service.list_known_locations 的取法保持一致。"""
    return str(scene.get("title") or scene.get("name") or scene.get("id") or "")


def _find_scene(module: Module, ref: str) -> dict | None:
    """按 id 或名字找地点。KP 表单里两种都可能填进来（下拉给 id，手打给名字）。

    只认 location：公开、挪人、配战场底图的对象都必须是能去的地方，事件/章节不算。
    """
    ref = str(ref or "").strip()
    if not ref:
        return None
    locs = scene_kind.location_scenes(module.scenes)
    for s in locs:
        if str(s.get("id") or "") == ref:
            return s
    for s in locs:
        if _scene_label(s) == ref or str(s.get("name") or "") == ref:
            return s
    return None


def _exec_unlock_place(
    db: Session, session_id: str, game_session: GameSession, module: Module, ref: str,
) -> tuple[list[str], str]:
    """把一个地点对玩家公开：从此出现在大地图上，可以前往。

    此前这件事只有两条路径：玩家走到过，或者某段**玩家可见**的叙述恰好提到了它
    （见 navigation_service.known_scene_ids）。真人 KP 想直接放一个地方出来，只能
    绕着写一句旁白去碰关键词匹配——碰不碰得上全看措辞。这里给他一个确定性的开关。
    """
    scene = _find_scene(module, ref)
    if scene is None:
        raise ValueError("找不到这个地点")
    sid = str(scene.get("id") or "")
    ws = dict(game_session.world_state or {})
    revealed = list(ws.get("kp_revealed") or [])
    name = _scene_label(scene)
    if sid in revealed:
        return [], f"{name} 早已对玩家公开"
    revealed.append(sid)
    ws["kp_revealed"] = revealed
    game_session.world_state = ws
    db.commit()
    # 落一条玩家可见的系统事件：地图上凭空多出一个地点而没有任何交代，
    # 玩家只会以为是 bug。
    return [event_to_chunk(session_service.add_event(
        db, session_id, "system", f"地图上多了一处去向：{name}",
        actor_name="KP", metadata={"kp_manual": True, "unlocked_scene": sid},
    ))], f"{name} 已对玩家公开"


def _exec_move_actor(
    db: Session, session_id: str, game_session: GameSession, module: Module,
    char_id: str, ref: str,
) -> tuple[list[str], str]:
    """把某个角色挪到另一个场景（KP 侧的强制移动）。

    玩家自己走是「前往」，要过封路、连通性这些闸；KP 挪人是裁定结果——被拖走、被传送、
    昏迷后醒在别处——不该被同一套闸拦下。所以这里直接写落点，不走 travel 那条链。
    目的地顺带对玩家公开：人都在那儿了，地图上还藏着它没有意义。

    只挪选中的这一个人。房主的位置就是 ``current_scene_id``，同处、没有位置记录的队友
    会随之被回落「带走」，所以经 ``movement.move_characters`` 落位：它在同一笔事务里先写实
    这些人的原位置（与自然语言移动同一实现），且不做通行校验。
    """
    scene = _find_scene(module, ref)
    if scene is None:
        raise ValueError("找不到目的地")
    if not char_id:
        raise ValueError("没有指定要移动谁")
    char = db.get(Character, char_id)
    if char is None:
        raise ValueError("找不到这个角色")
    sid = str(scene.get("id") or "")
    name = _scene_label(scene)
    party = session_service.get_party_members(db, session_id)
    if char not in party:
        party.append(char)
    movement.move_characters(db, session_id, game_session, module, [char], sid, party, announce=False)
    ws = dict(game_session.world_state or {})
    revealed = list(ws.get("kp_revealed") or [])
    if sid not in revealed:
        revealed.append(sid)
        ws["kp_revealed"] = revealed
        game_session.world_state = ws
        db.commit()
    return [event_to_chunk(session_service.add_event(
        db, session_id, "system", f"{char.name} 现在在{name}。",
        actor_name="KP", metadata={"kp_manual": True, "moved_to": sid, "char_id": char_id},
    ))], f"{char.name} 已移动到 {name}"
