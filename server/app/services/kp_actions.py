"""KP 的 NPC、追逐、战斗和结构化台词动作。"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.models.character import Character
from app.models.module import Module
from app.models.session import GameSession
from app.services import illustration_service, npc_identity, session_service, turn_context
from app.services.event_protocol import make_chunk as _make_chunk

_attach_npc_portrait = illustration_service._attach_npc_portrait
_maybe_encounter_illustration = illustration_service._maybe_encounter_illustration
_apply_world_memory = turn_context._apply_world_memory
_scene_title = turn_context._scene_title


async def _exec_npc_act(
    db: Session, session_id: str, game_session: GameSession, module: Module,
    llm, player_char: Character, npc_id: str, trigger: str,
    target: str = "", verb: str = "",
) -> tuple[list[str], str]:
    """兼容入口：统一委托给 NpcRuntime，避免又一条平行 NPC 路径。"""
    from app.services.npc_runtime import NpcRuntime
    return await NpcRuntime(
        db, session_id, game_session, module, llm, player_char,
    ).reply(npc_id, trigger, target=target, verb=verb)


def _exec_start_chase(
    db: Session, session_id: str, module: Module, player_char: Character,
    pursuer_str: str, trigger: str,
) -> list[str]:
    """start_chase 工具：玩家作逃方，pursuer 按名字解析模组 NPC（匹配不到按临场追兵建）。返回 chunks。"""
    from app.services import chase_service, rule_options_service

    session = db.get(GameSession, session_id)
    if session is not None and chase_service.get_chase(session):
        return []  # 重复提案不能重置距离、轮次或已经结算的行动。
    nm = (pursuer_str or "").strip() or "追兵"
    spec = next((n for n in (module.npcs or []) if n.get("name") == nm or n.get("id") == nm), None)
    pursuer = chase_service._pursuer_from_npc(
        spec or {"name": nm, "attributes": {"DEX": 50, "CON": 50, "SIZ": 50}, "skills": {"运动": 45}},
        rule_options_service.ruleset_by_id(db, session_id),
    )
    quarry = chase_service._quarry_from_char(player_char)
    _state, chunks = chase_service.start_chase(db, session_id, quarry, pursuer, trigger=trigger)
    return chunks


async def _exec_start_combat(
    db: Session, session_id: str, game_session: GameSession, module: Module,
    player_char: Character, teammates: list[Character] | None, llm,
    enemies_str: str, trigger: str, participant_ids: list[str] | None = None,
) -> list[str]:
    """start_combat 工具：按敌方名字解析模组 NPC，把选中的调查员与敌方切入战斗态。

    participant_ids 为空＝旧行为（主角+全部队友）；真人 KP 可只选一部分人入战，
    其余席位留在场外，不会被拉进先攻序。名字匹配不到的敌方按临场杂兵建（默认属性）。
    """
    from app.ai.agents.combat_agent import CombatAgent
    from app.services import combat_service

    if combat_service.get_combat(game_session):
        return []  # 计划、工具与补偿守卫共用此入口，已开战时不得覆盖战斗态。
    names = [n.strip() for n in re.split(r"[，,、]", enemies_str or "") if n.strip()]
    npc_by = {n.get("name"): n for n in (module.npcs or [])}
    npc_by_id = {n.get("id"): n for n in (module.npcs or [])}
    enemies: list[dict] = []
    for nm in names or ["敌人"]:
        spec = npc_by.get(nm) or npc_by_id.get(nm)
        enemies.append(dict(spec) if spec else {"name": nm, "attributes": {"DEX": 50, "CON": 50, "SIZ": 50},
                                                "skills": {"格斗(斗殴)": 45, "闪避": 25}, "weapon": "徒手格斗"})
    party = [player_char] + list(teammates or [])
    wanted = [str(ref).strip() for ref in (participant_ids or []) if str(ref).strip()]
    if wanted:
        known = {str(member.id) for member in party}
        unknown = [ref for ref in wanted if ref not in known]
        if unknown:
            raise ValueError("参战角色不在当前队伍里")
        party = [member for member in party if str(member.id) in set(wanted)]
        if not party:
            raise ValueError("至少选择一名参战调查员")
    human_ids = session_service.human_character_ids(db, session_id) or {player_char.id}
    if wanted:
        selected_ids = {str(member.id) for member in party}
        human_ids = {str(item) for item in human_ids if str(item) in selected_ids}
    scene_hint = _scene_title(module, game_session.current_scene_id)
    # 遭遇配图卡先落先播（战斗态 chunks 紧随其后）：卡先出、图异步补挂，不阻塞开战
    illust_chunks = _maybe_encounter_illustration(db, session_id, module, enemies)
    agent = CombatAgent(llm) if llm is not None else None
    _state, chunks = await combat_service.start(
        db, session_id, party, enemies, human_ids, trigger,
        agent=agent, scene_hint=scene_hint,
        masker=npc_identity.build_masker(db, session_id, module),
    )
    return illust_chunks + chunks


def _exec_say(
    result: list, module: Module, who: str, text: str, masker=None,
) -> list[str]:
    """say() 工具：把一句 NPC 台词作为对话气泡广播，并**记入 result 的对话交错标记**——
    落库交给收尾的 _persist_narration 按偏移与旁白交错持久化（复用旧路径的成熟机制），
    从而在 resync 时与旁白保持正确先后顺序（不能在 loop 中直接落库，那会先于旁白）。

    who 尽量归一到模组 NPC 的规范名；解析不到按临场龙套用原名。返回待广播的 chunks。
    此路径不经台词过滤器的启发式猜测——对话直接由模型的结构化调用给出。
    """
    npc_name = who
    for n in (module.npcs or []):
        if n.get("id") == who or n.get("name") == who:
            npc_name = n.get("name") or who
            break
    # 气泡上的说话人是玩家可见面：玩家还没认出这东西时不能报出真名。
    # 判据要带上**本轮到此为止玩家已经看到的正文**：masker 读的是库里的快照，而本轮旁白
    # 要到回合收尾 _persist_narration 才落库、本轮台词也还只在 result 里攒着。不带上就会
    # 出现「旁白刚写完『诺特先生放下账本』，紧接着的气泡还叫他陌生男性」，以及自报家门
    # 那一刻的「不明存在：我叫香澄澪」。
    if masker is not None:
        seen = [result[0] if result else ""]
        if len(result) > 2:
            seen += [t for _who, t in result[2]]
        seen.append(text)
        npc_name = masker(npc_name, extra_prose="\n".join(s for s in seen if s))
    # 偏移＝此刻已累计旁白长度（本步旁白已并入 result[0]）→ 台词插在本步旁白之后、下步之前。
    offset = len(result[0])
    if len(result) > 3:
        result[3].append((offset, npc_name, text))
    if len(result) > 2:
        result[2].append((npc_name, text))  # 供 _record_npc_say_memory 记入 NPC 互动史
    return [_make_chunk("dialogue", text, actor_name=npc_name)]
