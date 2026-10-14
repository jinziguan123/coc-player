"""真人 KP 工具桌到确定性领域动作的适配。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.character import Character
from app.models.module import Module
from app.models.session import GameSession
from app.rules.registry import get_engine
from app.services import (
    combat_service,
    dice_runtime,
    human_kp_service,
    illustration_service,
    kp_actions,
    navigation_service,
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
        actor_name = str(npc.get("name") or ref) if npc else ref
        # 台词里的小括号同样不算台词——「（他其实在说谎）」这种给同桌看的注解，
        # 不该被 NPC 说出口，也不该进上下文当成他真讲过的话。
        line, aside = split_ooc(content)
        if not line:
            raise ValueError("台词内容不能为空——整句都在小括号里的话，用叙事框发场外话")
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
        return chunks, "NPC 台词已发布" if not aside else "台词已发布，场外话另发了一条"

    if action == "dice_check":
        kv = {str(k): str(v) for k, v in payload.items() if v is not None}
        if not kv.get("skill", "").strip():
            raise ValueError("检定技能不能为空")
        chunks, descs, pending = await _exec_dice_check(
            db, session_id, game_session, module, kv, player_char, teammates,
        )
        return chunks, "已发起待投检定" if pending else ("；".join(descs) or "检定已结算")

    if action == "opposed_check":
        descs: list[str] = []
        chunks = [
            c async for c in _resolve_opposed(
                db, session_id, payload, get_engine(module.rule_system),
                module, player_char, teammates, descs,
            )
        ]
        if not descs:
            raise ValueError("对抗检定至少需要双方角色与技能")
        return chunks, "；".join(descs)

    if action == "generic_roll":
        return _exec_generic_roll(db, session_id, module, payload)

    if action == "scene_change":
        chunks, sid, note = await _exec_scene_change(
            db, session_id, game_session, module,
            str(payload.get("scene_id") or payload.get("scene") or "").strip(),
            player_char, teammates,
        )
        return chunks, note or (f"已切换至 {_scene_name(module, sid)}" if sid else "场景未变化")

    if action in ("set_flag", "clear_flag"):
        flag = str(payload.get("flag") or "").strip()
        if not flag:
            raise ValueError("剧情标志不能为空")
        return _exec_flag(db, session_id, game_session, flag, action == "set_flag"), "剧情标志已更新"

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
        chunks = await _exec_hp_change(
            db, session_id, player_char,
            str(payload.get("target") or ""), str(payload.get("delta") or ""),
            str(payload.get("reason") or ""), module=module, teammates=teammates,
        )
        if not chunks:
            raise ValueError("HP 目标或变化值无效")
        return chunks, "HP 已结算"

    if action == "san_check":
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
        chunks = await _exec_start_combat(
            db, session_id, game_session, module, player_char, teammates,
            None, enemies, str(payload.get("trigger") or "真人 KP 发起战斗"),
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

    raise ValueError(f"不支持的 KP 动作：{action}")


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
    """按 id 或名字找场景。KP 表单里两种都可能填进来（下拉给 id，手打给名字）。"""
    ref = str(ref or "").strip()
    if not ref:
        return None
    for s in (module.scenes or []):
        if str(s.get("id") or "") == ref:
            return s
    for s in (module.scenes or []):
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
    navigation_service.set_char_location(db, session_id, char_id, sid)
    db.refresh(game_session)
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
