"""把回合规划器的结构化裁定确定性落实为领域副作用。"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator

from sqlalchemy.orm import Session

from app.ai import turn_planner
from app.models.character import Character
from app.models.module import Module
from app.models.session import GameSession
from app.services import (
    dice_runtime,
    inventory_service,
    kp_actions,
    session_service,
    turn_context,
    turn_effects,
)
from app.services.event_protocol import make_chunk as _make_chunk

logger = logging.getLogger(__name__)
_current_turn_events = turn_context._current_turn_events
_exec_start_combat = kp_actions._exec_start_combat
_exec_san_check = turn_effects._exec_san_check
_exec_hp_change = turn_effects._exec_hp_change
_resolve_hp_target = turn_effects._resolve_hp_target
_exec_scene_change = turn_effects._exec_scene_change

_META_CLUE_LOCATION_WORDS = ("位置", "下落", "提示", "传闻", "文字", "内容", "线索")

# 这些词只表示讨论、建议或假设，不能作为「已经移动」的证据。
_NON_COMMITTAL_MOVE_MARKERS = (
    "要不要", "是否", "能不能", "可以吗", "去不去", "要去吗", "建议", "提议",
    "考虑", "打算", "想去", "想进入", "希望", "准备", "计划", "应该", "不如", "再决定", "如果",
)
_MOVE_VERBS = (
    "进入", "走进", "前往", "去往", "前去", "赶往", "抵达", "到达", "来到",
    "走向", "移动到", "返回", "回到", "穿过", "去",
)
_ENTRY_MARKERS = ("进入", "走进", "抵达", "到达", "来到", "踏入")
_TERROR_MARKERS = (
    "尸体", "尸骸", "腐尸", "血腥", "鲜血", "怪物", "生物", "畸形", "触手",
    "大嘴", "超自然", "非人的", "肢体", "头颅", "残骸", "肉块", "鬼魂", "幽灵",
    "邪神", "异形", "尖牙", "眼球", "面孔裂", "未来的日期", "明天的日期",
    "日期不对", "时间异常", "尚未发生", "预言成真", "不该存在",
)
_READ_TRIGGER_WORDS = ("阅读", "翻看", "查看", "读", "传阅")
_READ_EVIDENCE_WORDS = (
    "阅读", "翻看", "查看", "看看", "看完", "读", "扫读", "扫完", "接过", "传阅", "递给",
)
_READABLE_SOURCE_TERMS = (
    "报纸", "日记", "信件", "便签", "手稿", "书籍", "档案", "文件", "照片", "录像", "录音",
)


def _scene_terms(module: Module | None, scene_id: str | None) -> set[str]:
    """返回场景 id 与名称，供移动证据做保守的文本匹配。"""
    if not module or not scene_id:
        return set()
    terms = {str(scene_id).strip()}
    for scene in module.scenes or []:
        if str(scene.get("id") or "").strip() != str(scene_id).strip():
            continue
        for key in ("name", "title"):
            value = str(scene.get(key) or "").strip()
            if value:
                terms.add(value)
    return {term for term in terms if term}


def _guard_turn_events(
    db: Session, session_id: str, pre_gen_seq: int | None = None,
) -> list:
    """取生成前一段玩家回合及其后果。

    生成结束后最新的 KP narration 会让 ``_current_turn_events`` 只看到尾部系统事件；
    守卫仍需看到 narration 之前的玩家移动/行动，因此按生成前序号定位上一段旁白。
    """
    all_events = session_service.get_session_events(db, session_id)
    if pre_gen_seq is None:
        return _current_turn_events(all_events)
    previous_narr_seq = max(
        (int(event.sequence_num or 0) for event in all_events
         if event.event_type == "narration" and int(event.sequence_num or 0) <= pre_gen_seq),
        default=-1,
    )
    return [event for event in all_events if int(event.sequence_num or 0) > previous_narr_seq]


def _turn_evidence_text(
    db: Session, session_id: str, pre_gen_seq: int,
) -> str:
    """取本轮行动/对话与生成后新增叙事，避免拿旧回合的恐怖内容重复触发。"""
    events = _guard_turn_events(db, session_id, pre_gen_seq)
    texts = []
    for event in events:
        seq = int(event.sequence_num or 0)
        if event.event_type in ("action", "dialogue") or seq > pre_gen_seq:
            content = (event.content or "").strip()
            if content:
                texts.append(content)
    return "\n".join(texts)


def _explicit_player_movement(
    db: Session,
    session_id: str,
    module: Module | None,
    player_char: Character,
    target_scene_id: str,
    pre_gen_seq: int | None = None,
) -> bool:
    """判断本轮是否有玩家角色明确执行了前往目标场景的动作。

    只认玩家角色自己的 action/dialogue 或带 travel 元数据的动作；队友的建议、玩家的
    疑问与条件句全部拒绝。地图 travel 本身是权威证据，普通对话只有同时命中地点和移动动词才算。
    """
    terms = _scene_terms(module, target_scene_id)
    if not terms:
        return False
    events = _guard_turn_events(db, session_id, pre_gen_seq)
    for event in events:
        if event.actor_id != player_char.id:
            continue
        metadata = event.metadata_ or {}
        travel_scene = str(metadata.get("scene_id") or "").strip()
        if metadata.get("travel") and travel_scene == target_scene_id:
            return True
        if event.event_type not in ("action", "dialogue"):
            continue
        text = (event.content or "").strip()
        if not text or "?" in text or "？" in text:
            continue
        if any(marker in text for marker in _NON_COMMITTAL_MOVE_MARKERS):
            continue
        if not any(term in text for term in terms):
            continue
        if any(verb in text for verb in _MOVE_VERBS):
            return True
    return False


def _trigger_matches(trigger: str, text: str) -> bool:
    """允许模组机制 trigger 与叙事有轻微活用差异，但不把单个泛词当命中。"""
    trigger = (trigger or "").strip()
    text = (text or "").strip()
    if not trigger or not text:
        return False
    if trigger in text:
        return True
    # 「阅读报纸时」这类机制不能只做整句包含匹配。玩家常说“接过报纸扫完标题和日期”或
    # “递给队友看看”；要求同一可读实体与阅读动作在近距离内同时出现，避免仅看见物品就触发。
    readable_terms = [term for term in _READABLE_SOURCE_TERMS if term in trigger]
    if readable_terms and any(word in trigger for word in _READ_TRIGGER_WORDS):
        action = "|".join(map(re.escape, _READ_EVIDENCE_WORDS))
        for term in readable_terms:
            obj = re.escape(term)
            if re.search(rf"(?:{action}).{{0,24}}{obj}|{obj}.{{0,24}}(?:{action})", text):
                return True
    fragments = re.findall(r"[\u4e00-\u9fff]{2,}", trigger)
    if not fragments:
        return trigger.casefold() in text.casefold()
    hits = sum(1 for fragment in fragments if fragment in text)
    return hits >= (2 if len(fragments) >= 2 else 1)


def _scene_sanity_mechanism(
    module: Module | None,
    scene_id: str | None,
    evidence_text: str,
    entered: bool,
) -> dict | None:
    """找出本轮实际命中的当前场景 SAN 机制；进入场景时优先选择 entry 机制。"""
    if not module or not scene_id:
        return None
    scene = next(
        (s for s in module.scenes or [] if str(s.get("id") or "") == str(scene_id)),
        None,
    )
    mechanisms = [
        (index, event)
        for index, event in enumerate((scene or {}).get("events", []) or [])
        if isinstance(event, dict) and event.get("kind") == "san_check"
    ]
    if not mechanisms:
        return None
    for index, event in mechanisms:
        if _trigger_matches(str(event.get("trigger") or ""), evidence_text):
            matched = dict(event)
            matched["_source_key"] = dice_runtime._san_mechanism_source_key(
                str(scene_id), index,
            )
            return matched
    if entered:
        entry = [
            (index, event) for index, event in mechanisms
            if any(marker in str(event.get("trigger") or "") for marker in _ENTRY_MARKERS)
        ]
        if len(entry) == 1:
            index, event = entry[0]
            matched = dict(event)
            matched["_source_key"] = dice_runtime._san_mechanism_source_key(
                str(scene_id), index,
            )
            return matched
        if len(mechanisms) == 1:
            index, event = mechanisms[0]
            matched = dict(event)
            matched["_source_key"] = dice_runtime._san_mechanism_source_key(
                str(scene_id), index,
            )
            return matched
    return None


def _apply_scene_sanity_mechanism(plan: turn_planner.TurnPlan, mechanism: dict) -> bool:
    """把模组原文的 ``san_loss=成功/失败`` 规格覆盖到计划，拒绝通用默认骰式。"""
    raw = str(mechanism.get("san_loss") or "").strip()
    parts = re.split(r"\s*/\s*", raw, maxsplit=1)
    if len(parts) != 2 or not all(part.strip() for part in parts):
        return False
    plan.sanity.trigger = True
    plan.sanity.success_loss = parts[0].strip()
    plan.sanity.failure_loss = parts[1].strip()
    plan.sanity.source = str(
        mechanism.get("_source_key")
        or mechanism.get("trigger")
        or plan.sanity.source
        or "场景机制"
    ).strip()
    return True


def _sanity_has_evidence(
    db: Session,
    session_id: str,
    game_session: GameSession,
    module: Module | None,
    player_char: Character,
    plan: turn_planner.TurnPlan,
    pre_gen_seq: int,
) -> bool:
    """SAN 只能由本轮恐怖叙事或当前场景明文机制触发。"""
    evidence_text = _turn_evidence_text(db, session_id, pre_gen_seq)
    scene_id = session_service.get_char_location(game_session, player_char.id)
    entered = bool(scene_id and _explicit_player_movement(
        db, session_id, module, player_char, scene_id, pre_gen_seq=pre_gen_seq,
    ))
    mechanism = _scene_sanity_mechanism(module, scene_id, evidence_text, entered)
    if mechanism is not None and _apply_scene_sanity_mechanism(plan, mechanism):
        return True
    if not plan.sanity.trigger or not evidence_text:
        return False
    if not any(marker in evidence_text for marker in _TERROR_MARKERS):
        return False
    source = (plan.sanity.source or "").strip()
    if not source or source in ("本轮目睹的恐怖", "恐怖", "未知恐怖"):
        return True
    return _trigger_matches(source, evidence_text) or any(
        marker in source and marker in evidence_text for marker in _TERROR_MARKERS
    )


def _canonical_item_scene_ids(module: Module | None, item: turn_planner.ItemDelta) -> set[str]:
    """从结构化场景/实体线索中提取物品的正典出现地点；没有锚点时返回空集并保持兼容。"""
    if module is None:
        return set()
    name = (item.name or "").strip().casefold()
    if not name:
        return set()
    is_key = item.kind == "key" or "钥匙" in name or name == "key"
    is_document = item.kind == "document"
    if not (is_key or is_document):
        return set()
    terms = {name}
    # 只有模型确实只写了泛称“钥匙”时才用类型词回退。具体名称（如“酒店房门钥匙”）
    # 若在模组中没有同名锚点，应视为无法确定而放行，避免误伤合理的临场普通物品。
    if name in ("钥匙", "key", "一把钥匙"):
        terms.update(("钥匙", "key"))

    def _matches(text: str) -> bool:
        haystack = (text or "").casefold()
        return any(term and term in haystack for term in terms)

    scene_ids: set[str] = set()
    for scene in module.scenes or []:
        sid = str(scene.get("id") or "").strip()
        if sid and _matches(str(scene.get("description") or "")):
            scene_ids.add(sid)

    # 线索名像“驾驶室钥匙/报纸”时代表实体；“钥匙的位置/某段文字”只是信息，不作为物品落点。
    for clue in module.clues or []:
        clue_name = str(clue.get("name") or "")
        location = str(clue.get("location") or "").strip()
        if (
            location
            and _matches(clue_name)
            and not any(word in clue_name for word in _META_CLUE_LOCATION_WORDS)
        ):
            scene_ids.add(location)
    return scene_ids


def enforce_plan_item_locations(
    plan: turn_planner.TurnPlan | None,
    module: Module | None,
    current_scene_id: str | None,
    character_scene_ids: dict[str, str | None] | None = None,
) -> list[str]:
    """剔除与正典地点冲突的物品获得，并把冲突转成给 KP 的硬叙事约束。"""
    if plan is None or not plan.items_gained or not current_scene_id:
        return []
    kept: list[turn_planner.ItemDelta] = []
    blocked: list[str] = []
    for item in plan.items_gained:
        anchors = _canonical_item_scene_ids(module, item)
        item_scene_id = (character_scene_ids or {}).get(item.who) or current_scene_id
        if anchors and item_scene_id not in anchors:
            blocked.append(
                f"{item.name} 的正典地点为 {', '.join(sorted(anchors))}，当前是 {item_scene_id}"
            )
        else:
            kept.append(item)
    if not blocked:
        return []

    plan.items_gained = kept
    plan.narration_brief.append(
        "事实硬约束：当前场景与关键物品的结构化正典地点冲突；不得写成在这里找到、获得或复制该物品。"
        "应让本轮结果排除错误猜测，或只给出不泄露答案的正确方向。"
    )
    logger.warning("规划器物品地点冲突，已阻止写入：%s", "；".join(blocked))
    return blocked


async def _ensure_planned_combat(
    db: Session,
    session_id: str,
    game_session: GameSession,
    module: Module,
    player_char: Character,
    teammates: list[Character] | None,
    llm,
    plan: turn_planner.TurnPlan | None,
) -> AsyncIterator[str]:
    """确保规划器裁定的开战一定落成战斗态，补偿 KP 漏调工具或旧指令。

    模型仍负责识别「是否开战、敌方是谁」；一旦结构化计划给出肯定裁定，状态切换就由
    后端确定性保证。若 KP 已经通过工具或文本指令创建战斗，本守卫幂等返回。
    """
    if plan is None or not plan.combat.should_start:
        return

    from app.services import combat_service

    current = combat_service.get_combat(db.get(GameSession, session_id))
    if current and current.get("active"):
        return

    enemies = "，".join(plan.combat.enemies)
    trigger = plan.combat.trigger.strip() or plan.player_intent.strip() or "冲突升级为正面交战"
    if not enemies:
        logger.warning("规划器裁定开战但未给敌方名字，使用临场敌人兜底: session=%s", session_id)
    chunks = await _exec_start_combat(
        db, session_id, game_session, module, player_char, teammates, llm, enemies, trigger,
    )
    for chunk in chunks:
        yield chunk

def _san_rolled_this_turn(db: Session, session_id: str, pre_gen_seq: int) -> bool:
    """本轮是否已结算或发起过 SAN；防止确定性守卫重复发检定。"""
    for ev in session_service.get_session_events(db, session_id):
        meta = ev.metadata_ or {}
        if (
            (ev.sequence_num or 0) > pre_gen_seq
            and (
                (ev.event_type == "dice" and meta.get("skill") == "SAN")
                or (
                    ev.event_type == "system"
                    and meta.get("check_request")
                    and meta.get("kind") == "san_check"
                )
            )
        ):
            return True
    return False


async def _ensure_planned_sanity(
    db: Session,
    session_id: str,
    game_session: GameSession,
    player_char: Character,
    teammates: list[Character] | None,
    plan: turn_planner.TurnPlan | None,
    pre_gen_seq: int,
    module: Module | None = None,
) -> AsyncIterator[str]:
    """确保规划器裁定的『目睹恐怖』一定落成理智检定，补偿 KP 漏发 SAN_CHECK。

    模型仍负责识别本轮是否目睹恐怖及其强度；一旦结构化计划肯定裁定，SAN 检定就由后端确定性
    发出（真人挂待投请求，AI 角色自动结算）。若 KP 本轮已自行发起 SAN（任意恐怖源），本守卫幂等跳过；
    同一角色对同一恐怖源的去重仍由 _exec_san_check（world_state.san_checked）保证。
    """
    plan = plan or turn_planner.TurnPlan()
    if not _sanity_has_evidence(
        db, session_id, game_session, module, player_char, plan, pre_gen_seq,
    ):
        logger.warning(
            "规划器 SAN 缺少本轮恐怖证据，已跳过：session=%s source=%s",
            session_id, plan.sanity.source,
        )
        return
    # 结构化场景机制使用稳定 source_key，_exec_san_check 可逐角色跳过已结算/待投项并补齐遗漏者；
    # 非结构化来源仍保留旧的整轮守卫，避免 KP 与 planner 对同一恐怖使用不同自由文本时重复扣。
    if (
        _san_rolled_this_turn(db, session_id, pre_gen_seq)
        and not plan.sanity.source.startswith("scene:")
    ):
        return
    kv = {
        "success_loss": plan.sanity.success_loss or "0",
        "failure_loss": plan.sanity.failure_loss or "1d6",
        "source": (plan.sanity.source or "本轮目睹的恐怖").strip(),
        "chars": "/".join(plan.sanity.witnesses) if plan.sanity.witnesses else "",
    }
    chunks, _descs, _pending = await _exec_san_check(
        db, session_id, game_session, kv, player_char, teammates,
    )
    for chunk in chunks:
        yield chunk


def _hp_changed_this_turn(db: Session, session_id: str, pre_gen_seq: int) -> bool:
    """本轮生成里是否已发生过**扣血**事件（KP 自发 HP_CHANGE 或先前守卫）——用于让大失败反噬守卫
    在 KP 已自行扣血时幂等跳过，不重复伤害。只认伤害（hp_change<0），治疗不算。"""
    for ev in session_service.get_session_events(db, session_id):
        if (ev.sequence_num or 0) > pre_gen_seq and ((ev.metadata_ or {}).get("hp_change") or 0) < 0:
            return True
    return False


async def _ensure_planned_mishap(
    db: Session,
    session_id: str,
    player_char: Character,
    teammates: list[Character] | None,
    plan: turn_planner.TurnPlan | None,
    pre_gen_seq: int,
    module: Module | None = None,
) -> AsyncIterator[str]:
    """确保规划器裁定的『大失败身体反噬』一定落成扣血，补偿 KP 漏发 HP_CHANGE。

    仅大失败且所做动作本身有身体危险时，planner 才置 mishap.trigger（图书馆/话术等无害失败不触发）。
    KP 本轮已自行扣过血则幂等跳过，不重复伤害；受伤者按 plan 指定，缺省本轮掷骰玩家。
    """
    if plan is None or not plan.mishap.trigger:
        return
    delta = int(plan.mishap.hp_delta or 0)
    if delta >= 0:                                          # 恒为伤害；非负=无有效反噬
        return
    if _hp_changed_this_turn(db, session_id, pre_gen_seq):  # KP 已自行扣血 → 不重复
        return
    target = (plan.mishap.target or player_char.name).strip()
    reason = (plan.mishap.reason or "大失败反噬").strip()
    chunks = await _exec_hp_change(
        db, session_id, player_char, target, str(delta), reason,
        module=module, teammates=teammates,
    )
    for chunk in chunks:
        yield chunk


async def _ensure_planned_items(
    db: Session,
    session_id: str,
    game_session: GameSession,
    player_char: Character,
    teammates: list[Character] | None,
    plan: turn_planner.TurnPlan | None,
    pre_gen_seq: int | None = None,
) -> AsyncIterator[str]:
    """规划器裁定的物品增减确定性落库（获得入库、失去/消耗移除），补偿 KP 不记账——库存是权威状态。

    幂等：按「本轮玩家行动锚序号 + 获/失 + 名字 + 角色」去重（存 world_state.item_delta_keys），
    重新生成不会重复增减。物品效果仍由 KP 叙述——这里只保证库存数目可靠。
    """
    if plan is None or (not plan.items_gained and not plan.items_lost):
        return
    module = db.get(Module, game_session.module_id)
    target_scene = game_session.current_scene_id
    character_scene_ids = {
        char.name: session_service.get_char_location(game_session, char.id)
        for char in [player_char, *(teammates or [])]
    }
    enforce_plan_item_locations(
        plan, module, target_scene, character_scene_ids=character_scene_ids,
    )
    if not plan.items_gained and not plan.items_lost:
        return
    turn = _current_turn_events(session_service.get_session_events(db, session_id))
    anchor = max(
        (e.sequence_num or 0 for e in turn if e.event_type in ("action", "dialogue")),
        default=0,
    )
    ws = dict(game_session.world_state or {})
    done = set(ws.get("item_delta_keys") or [])
    changed = False

    def _who(name: str) -> Character:
        return _resolve_hp_target((name or "").strip(), player_char, teammates) or player_char

    for ig in plan.items_gained:
        name = (ig.name or "").strip()
        if not name:
            continue
        target = _who(ig.who)
        key = f"g|{anchor}|{name}|{target.id}"
        if key in done:
            continue
        inventory_service.add_item(db, target, name, qty=ig.qty or 1, kind=(ig.kind or None))
        done.add(key); changed = True
        suffix = f"×{ig.qty}" if (ig.qty or 1) > 1 else ""
        ev = session_service.add_event(
            db, session_id, "system", f"{target.name} 获得了 {name}{suffix}",
            actor_name="系统", metadata={"item_gain": True, "char_id": target.id},
        )
        yield _make_chunk("system", ev.content, event_id=ev.id, metadata={"item_gain": True})
        yield _make_chunk("inventory_update", metadata={"char_id": target.id})

    for il in plan.items_lost:
        name = (il.name or "").strip()
        if not name:
            continue
        target = _who(il.who)
        key = f"l|{anchor}|{name}|{target.id}"
        if key in done:
            continue
        done.add(key); changed = True   # 记键即便无匹配，避免重生成反复尝试
        if inventory_service.remove_by_name(db, target, name, qty=il.qty or 1):
            ev = session_service.add_event(
                db, session_id, "system", f"{target.name} 失去了 {name}",
                actor_name="系统", metadata={"item_loss": True, "char_id": target.id},
            )
            yield _make_chunk("system", ev.content, event_id=ev.id, metadata={"item_loss": True})
            yield _make_chunk("inventory_update", metadata={"char_id": target.id})

    if changed:
        ws["item_delta_keys"] = list(done)
        game_session.world_state = ws
        db.commit()


async def _ensure_planned_combat_damage(
    db: Session,
    session_id: str,
    player_char: Character,
    plan: turn_planner.TurnPlan | None,
) -> AsyncIterator[str]:
    """战斗中非常规/范围攻击（燃烧弹/群体/环境）→ 引擎把伤害挂成玩家 pending_roll（亲手掷、
    应用到所有波及敌人）。仅战斗中生效；幂等（stage_aoe_damage 按行动锚 dedup_key 去重）。"""
    if plan is None or not plan.combat_damage.trigger or not plan.combat_damage.targets:
        return
    from app.services import combat_service
    if not combat_service.get_combat(db.get(GameSession, session_id)):
        return
    turn = _current_turn_events(session_service.get_session_events(db, session_id))
    anchor = max(
        (e.sequence_num or 0 for e in turn if e.event_type in ("action", "dialogue")), default=0)
    cd = plan.combat_damage
    chunk, staged = combat_service.stage_aoe_damage(
        db, session_id, player_char.id, list(cd.targets), cd.weapon, cd.formula, cd.burning,
        cd.reason, dedup_key=f"{anchor}|{'/'.join(cd.targets)}",
    )
    if staged and chunk:
        yield chunk


async def _ensure_planned_scene(
    db: Session,
    session_id: str,
    game_session: GameSession,
    module: Module,
    player_char: Character,
    teammates: list[Character] | None,
    plan: turn_planner.TurnPlan | None,
    pre_gen_seq: int | None = None,
) -> AsyncIterator[str]:
    """确保规划器裁定的『玩家本轮真实移动到某场景』一定落成位置/地图切换，补偿 KP 漏调 scene_change。

    这修复的是「KP 叙述了到达新场景，但大地图仍停在旧场景」——过去场景切换**只**靠 KP 记得发
    `[SCENE_CHANGE]`/`scene_change` 工具，漏发就地图与叙事脱节。现在与 SAN/战斗/库存一致：规划器
    给出明确目标场景，后端确定性把角色搬过去。

    幂等且保守：
    - KP 已自行切到目标场景 → `_exec_scene_change` 见位置已到位、原地返回，不重复切；
    - 目标解析不到真实场景 id/名 → 安全跳过（不写脏值、不回退到首个场景）；
    - 规划器仅在玩家**确实前往并到达**别处时才置此字段（『讨论/打算去』不置），语义与 KP 工具一致。
    """
    if plan is None:
        return
    ref = (plan.scene_policy.scene_change or "").strip()
    if not ref:
        return
    db.refresh(game_session)
    target_scene_id = turn_context._resolve_scene_ref(module, ref)
    current_scene_id = session_service.get_char_location(game_session, player_char.id)
    if (
        target_scene_id
        and target_scene_id != current_scene_id
        and not _explicit_player_movement(
            db, session_id, module, player_char, target_scene_id,
            pre_gen_seq=pre_gen_seq,
        )
    ):
        logger.warning(
            "规划器场景切换缺少玩家移动证据，已跳过：session=%s target=%s",
            session_id, target_scene_id,
        )
        return
    chunks, _sid, _note = await _exec_scene_change(
        db, session_id, game_session, module, ref, player_char, teammates,
    )
    for chunk in chunks:
        yield chunk
