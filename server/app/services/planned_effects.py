"""把回合规划器的结构化裁定确定性落实为领域副作用。"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator

from sqlalchemy.orm import Session

from app.ai import turn_planner
from app.models.character import Character
from app.models.module import Module
from app.models.session import GameSession
from app.models.session_ledger import SessionLedger
from app.services import (
    dice_runtime,
    inventory_service,
    kp_actions,
    session_service,
    turn_context,
    turn_effects,
    world_memory,
)
from app.services.event_protocol import make_chunk as _make_chunk

logger = logging.getLogger(__name__)
_current_turn_events = turn_context._current_turn_events
_exec_start_combat = kp_actions._exec_start_combat
_exec_san_check = turn_effects._exec_san_check
_exec_dice_check = turn_effects._exec_dice_check
_exec_hp_change = turn_effects._exec_hp_change
_resolve_hp_target = turn_effects._resolve_hp_target
_exec_scene_change = turn_effects._exec_scene_change

_META_CLUE_LOCATION_WORDS = ("位置", "下落", "提示", "传闻", "文字", "内容", "线索")

# 这些词只表示讨论、建议、假设或未来的可能，不能作为「已经移动」的证据。
# 「我们去图书馆看看吧」「之后我可能会去B」都落在这里：句中有「去」也只是意图。
_NON_COMMITTAL_MOVE_MARKERS = (
    "要不要", "是否", "能不能", "可以吗", "去不去", "要去吗", "建议", "提议",
    "考虑", "打算", "想去", "想进入", "希望", "准备", "计划", "应该", "不如", "再决定", "如果",
    "可能", "也许", "或许", "之后", "以后", "回头", "稍后", "待会", "待会儿", "过会儿",
    "等会", "等会儿", "晚点", "晚些", "过一阵", "看看再说", "再说", "看看吧", "吧",
)
_MOVE_VERBS = (
    "进入", "走进", "前往", "去往", "前去", "赶往", "抵达", "到达", "来到",
    "走向", "移动到", "返回", "回到", "穿过", "去",
)
_ENTRY_MARKERS = ("进入", "走进", "抵达", "到达", "来到", "踏入")
# 技能检定的进场机制点比 SAN 多认「前往/踏进」：模组常把「前往X时投灵感」写成预感式触发。
_ENTRY_CHECK_MARKERS = (*_ENTRY_MARKERS, "前往", "踏进")
# world_state 键：已发过的进场检定（逐角色逐机制点记一次，重开/重生成都不重复发）。
_ENTRY_CHECK_STATE_KEY = "scene_entry_checks"
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
    db: Session, session_id: str, pre_gen_seq: int, group_label: str | None = None,
) -> str:
    """取本轮行动/对话与生成后新增叙事，避免拿旧回合的恐怖内容重复触发。

    ``group_label`` 给定时（分头行动）剔除**明确属于别组**的事件：另一处场景发生的恐怖
    不是这一组的目睹证据，混在一起看会让守卫拿隔壁的怪物给这边的人补 SAN。
    单场景回合的事件没有分组标签，一律保留，行为不变。
    """
    events = _guard_turn_events(db, session_id, pre_gen_seq)
    texts = []
    for event in events:
        seq = int(event.sequence_num or 0)
        group = (event.metadata_ or {}).get("group")
        if group_label and group and group != group_label:
            continue
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
    """找出本轮实际命中的当前场景 SAN 机制；进入场景时才认写明「进场触发」的那条。

    命中顺序：先按 trigger 与本轮实际发生的事对文（``_trigger_matches``），对不上时，
    只有在「玩家本轮确实进场」且该机制的 trigger 明写进场标记（进入/走进/抵达…）时才补发。

    **绝不因为「这个场景只有一条 SAN 机制」就当成进场机制。** 曾经有过这条兜底，代价是
    5 号车厢那条 ``trigger="阅读报纸时"`` 的机制在玩家刚拉开隔门时就触发了——玩家没读报纸、
    甚至没走到报纸跟前，SAN 就先掉了。机制点的 trigger 是模组作者写死的判定条件，
    「只有一条」不构成把它改判成进场触发的理由；宁可漏发（KP 仍可在玩家真读报纸时自发
    [SAN_CHECK]），也不能凭空对玩家扣 SAN。
    """
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
    scene_id = session_service.get_char_location(game_session, player_char.id)
    # 守卫是主角这一组的视角：证据也只认这一组的（分头时别组的叙事另有其组标签）。
    evidence_text = _turn_evidence_text(
        db, session_id, pre_gen_seq,
        group_label=turn_context._scene_name(module, scene_id) if module and scene_id else None,
    )
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


def _narrated_turn_text(db: Session, session_id: str, pre_gen_seq: int) -> str:
    """本轮新生成的叙事与 NPC 台词——守卫据此核对「文字里到底发生了什么」。"""
    texts = []
    for ev in session_service.get_session_events(db, session_id):
        if int(ev.sequence_num or 0) <= pre_gen_seq:
            continue
        if ev.event_type in ("narration", "dialogue"):
            content = (ev.content or "").strip()
            if content:
                texts.append(content)
    return "\n".join(texts)


#: KP 明确裁定「这次不用掷」的措辞。命中就认它裁定过了，不再补挂检定。
#: 只认明说的——KP 若压根没提检定、直接写了段叙事，那不是裁定，是把玩家的申请顺过去了。
_NO_CHECK_PHRASES = (
    "无需检定", "不需要检定", "无须检定", "不必检定", "不用检定", "无需再检定",
    "无需掷骰", "不用掷骰", "不必掷骰", "无需骰", "不用投骰", "无需投骰",
)


def requested_check_settled(
    db: Session, session_id: str, actor_id: str, pre_gen_seq: int,
) -> bool:
    """本轮 KP 是否已经为该角色挂出待投检定（即：它对这次申请给了机制上的答复）。

    **不算数的**：系统守卫补的 SAN 检定（kind=san_check）——那是恐怖裁定，不是对
    「我要用格斗砸它」这次申请的答复。实测那一局正是这样：玩家申请格斗检定，KP 写了段
    叙事没发指令，SAN 守卫补了个理智检定，于是看起来「挂了检定」，玩家申请的那个却蒸发了。
    技能名换了不算问题（玩家申请侦查、KP 认为该用聆听，那是 KP 的裁定权）。
    """
    for ev in session_service.get_session_events(db, session_id):
        if int(ev.sequence_num or 0) <= pre_gen_seq:
            continue
        meta = ev.metadata_ or {}
        if not meta.get("check_request"):
            continue
        if meta.get("kind") == "san_check":
            continue
        if str(meta.get("char_id") or "") == str(actor_id):
            return True
    return False


def requested_check_fallback_command(
    db: Session, session_id: str, actor, skill: str, pre_gen_seq: int,
) -> str:
    """玩家亲口申请的检定被叙事顺过去时，合成一条 [DICE_CHECK] 交给既有指令管线补挂。

    玩家点「申请检定」是一次明确的机制请求，只该有两种归宿：掷骰，或 KP 明说这次不用掷。
    第三种——KP 写了段叙事、申请无声无息地消失——不可接受：玩家点的那一下等于没发生，
    更糟的是 KP 常顺手把结果也演了（实测「说服」那次直接演成说服成功，骰子根本没登场，
    等于替玩家判定了成败）。

    返回空串表示无需补挂（已挂过 / KP 明说免检 / 参数不全）。
    """
    skill = (skill or "").strip()
    if not skill or actor is None:
        return ""
    if requested_check_settled(db, session_id, getattr(actor, "id", ""), pre_gen_seq):
        return ""
    narration = _narrated_turn_text(db, session_id, pre_gen_seq)
    if any(phrase in narration for phrase in _NO_CHECK_PHRASES):
        return ""   # KP 明确裁定这次不用掷，尊重它的裁定权
    logger.info(
        "玩家申请的检定被叙事顺过去，确定性补挂：session=%s 角色=%s 技能=%s",
        session_id, getattr(actor, "name", ""), skill,
    )
    return f"[DICE_CHECK: skill={skill}, difficulty=normal, char={getattr(actor, 'name', '')}]"


#: 线索确实被「摆到玩家面前」的动作证据。只出现名字不算——KP 常顺笔带过环境里躺着块石板，
#: 那时玩家还什么都没看到，记进台账反而会让真正的发现桥段被当成重复而跳过。
_CLUE_EVIDENCE_WORDS = (
    "读", "看", "翻", "查", "扫", "照", "摸", "辨认", "发现", "认出", "拿起", "捡起",
    "写着", "刻着", "记着", "字迹", "刻痕", "内容", "翻开", "递给", "接过", "凑近", "拨开",
)

#: 机制点 trigger 里的触发动作/虚词，剥掉后剩下的才是「这条机制点讲的那个具名事物」。
_TRIGGER_ACTION_WORDS = (
    "阅读", "翻看", "查看", "看到", "看见", "读到", "进入", "走进", "抵达", "到达", "来到",
    "踏入", "前往", "踏进", "离开", "走出", "触碰", "接触", "发现", "调查", "搜索", "搜寻",
    "尝试", "试图", "的时候", "时", "后", "被", "中",
)


def _scene_event_narrated(trigger: str, text: str) -> bool:
    """本轮叙事有没有把这条机制点演出来。

    判据是「trigger 里的具名事物在叙事里出现了」：剥掉触发动作后剩下的实词命中一个即算。
    不能用 ``_trigger_matches``——机制点的 trigger 多是无标点长句（「进入最里面的小屋被拖拽」），
    那个函数对这种输入退化成整串包含匹配，永远命中不了。

    只认字面：KP 把「被拖拽」写成「那只手猛地一拽，你半个人被扯进门洞」时抓不住——
    语义改写超出文本匹配的能力边界，那类只能靠后端确定性发出机制时记的那本账。
    宁可漏标（KP 至多重演一次，与现状同）也不多标（多标会让没演的桥段被跳过）——
    但反过来，实词一旦出现就认，因为「重复」正是玩家最能直接感知的那种坏。
    """
    if not trigger or not text:
        return False
    core = trigger
    for word in _TRIGGER_ACTION_WORDS:
        core = core.replace(word, " ")
    terms = re.findall(r"[一-鿿]{2,}", core)
    return any(term in text for term in terms)


def _clue_aliases(clue: dict) -> list[str]:
    """线索名里可供文本匹配的中文片段（「绘本《摘瘤爷爷》」→ 绘本 / 摘瘤爷爷）。"""
    return re.findall(r"[一-鿿]{2,}", str(clue.get("name") or ""))


def _clue_shown_in_narration(clue: dict, text: str) -> bool:
    """本轮叙事里是否真把这条线索摆到了玩家面前（名字 + 动作证据近距离同现）。"""
    if not text:
        return False
    action = "|".join(map(re.escape, _CLUE_EVIDENCE_WORDS))
    for alias in _clue_aliases(clue):
        obj = re.escape(alias)
        if re.search(rf"(?:{action}).{{0,24}}{obj}|{obj}.{{0,24}}(?:{action})", text):
            return True
    return False


def record_narrated_progress(
    db: Session,
    session_id: str,
    game_session: GameSession,
    module: Module | None,
    player_char: Character,
    teammates: list[Character] | None,
    pre_gen_seq: int,
) -> None:
    """本轮叙事里实际发生了什么 → 确定性记进世界记忆（场景机制点 + 线索台账）。

    补的是「记账这件事本身也交给了 LLM」这个结构性缺口：线索台账此前唯一的写入口是规划器
    填的 ``clue_policy.candidate_clue_ids``（见 ``turn_context.record_clue_ledger_from_plan``），
    规划器没把「祠堂里那块石板」认回 ``clue_3``，账就永远不记。实测『闇暗山』那局跑了 6 个
    场景、187 条叙述，``clue_ledger`` 一条没有——于是「不要重复安排发现桥段」这句硬指示
    从头到尾没进过上下文，KP 对着线索明文重演，玩家在对话里当场回了句「这不就是你刚才
    和我说的内容嘛」。

    补挂一律只记 ``hint``（有所察觉）而非完整揭示：文本匹配必然有误差，宁可让 KP 觉得
    「玩家摸到了边角」而继续深入，也不能凭一次误匹配就把整条线索判成已掌握、从此再不揭示。
    规划器正常记账时仍会把它升级为「完全掌握」（``known`` 不降级）。

    fail-open：任何异常都只记日志，绝不阻塞出牌。
    """
    try:
        narration = _narrated_turn_text(db, session_id, pre_gen_seq)
        if not narration or module is None:
            return
        db.refresh(game_session)
        party = [player_char, *(teammates or [])]
        at = {c.id: session_service.get_char_location(game_session, c.id) for c in party}
        anchor = at.get(player_char.id)
        # 分头行动下信息不共享：知晓者只记与主角同场景的人（与规划器记账口径一致）。
        here = [c.id for c in party if at.get(c.id) == anchor]
        scene_ids = set(at.values()) | {str(game_session.current_scene_id or "")}
        scene_ids.discard("")
        scene_ids.discard(None)
        seq = session_service.get_next_sequence_num(db, session_id) - 1

        ws = dict(game_session.world_state or {})
        led = game_session.ledger
        seen = dict(led.scene_events_seen if led else {})
        before = json.dumps(
            [seen, ws.get("clue_ledger")], sort_keys=True,
        )

        # 场景机制点：模组写明的一次性桥段，叙事对上 trigger 就算演过了。
        # 遍历队伍所在的每个场景（不只当前场景）——分头行动时那一轮叙事覆盖多个场景。
        for scene in module.scenes or []:
            sid = str(scene.get("id") or "")
            if sid not in scene_ids:
                continue
            for index, event in enumerate(scene.get("events") or []):
                if not isinstance(event, dict):
                    continue
                trigger = str(event.get("trigger") or "").strip()
                if not trigger or world_memory.scene_event_seen(seen, sid, index):
                    continue
                if _scene_event_narrated(trigger, narration):
                    seen = world_memory.record_scene_event_seen(
                        seen, sid, index, seq, note=trigger,
                    )

        # 线索：只认玩家此刻所在场景（或无绑定场景）的，别把别处的线索凭一个同名词记掉。
        ledger = dict(ws.get("clue_ledger") or {})
        for clue in module.clues or []:
            cid = str((clue or {}).get("id") or "").strip()
            loc = str(clue.get("location") or "").strip()
            if not cid or cid in ledger or (loc and loc not in scene_ids):
                continue
            if _clue_shown_in_narration(clue, narration):
                ws = world_memory.record_clue_reveal(
                    ws, [cid], "hint", here, seq,
                    note=f"叙事已提及{clue.get('name') or cid}（确定性补挂）",
                )

        after = json.dumps(
            [seen, ws.get("clue_ledger")], sort_keys=True,
        )
        if before != after:
            if led is None:
                led = SessionLedger(session_id=game_session.id)
                db.add(led)
            led.scene_events_seen = seen
            game_session.world_state = ws
            db.commit()
    except Exception:
        logger.exception("叙事进度记账失败（忽略）：session=%s", session_id)


def _npcs_present(module: Module | None, game_session: GameSession) -> list[dict]:
    """当前场景在场（或无固定位置）的存活 NPC 简况，供判定认名字、认动机。"""
    if module is None:
        return []
    scene_id = str(game_session.current_scene_id or "").strip()
    present = []
    for npc in (module.npcs or []):
        loc = str(npc.get("initial_location") or "").strip()
        if loc and scene_id and loc != scene_id:
            continue
        if npc.get("alive") is False:
            continue
        name = str(npc.get("name") or "").strip()
        if not name:
            continue
        present.append({
            "name": name,
            "description": (str(npc.get("description") or ""))[:80],
            "goals": npc.get("goals") or [],
        })
    return present


# 「叙事已交战、机制没跟上」的预筛词表：KP 把怪物写成扑上来了，却既没调 start_combat、
# planner 也判了不开战——玩家读到「它扑过来了」，却没有先攻队列可打，它下一轮咬没咬到人
# 全看 KP 心情。词表只做**免费预筛**：不含这些词的轮次直接零成本跳过，命中才花一次快模型
# 确认。因此这里只收**实际接触到人的攻击动作**，「逼近/冲向/堵住」这类光靠移动的词不要——
# 它们在恐怖叙事里几乎每轮都有，收进来等于每轮都多掏一次调用。
# 单字词一律不收：「撕」会被撕开信封命中、「咬」会被咬紧牙关命中、「牙」更是直接被
# 「江户川龙牙」这样的角色名每轮命中——白掏一次调用。宁可靠 KP 几乎必写的扑/爪/咬住
# 这类词兜住，漏掉的生僻写法（「攫住」）等真遇上了再往词表里加。
_ENGAGE_MARKERS = (
    "扑来", "扑向", "扑上", "扑击", "猛扑", "扑倒", "扑咬", "咬住", "咬向", "咬穿",
    "撕咬", "撕碎", "獠牙", "利爪", "挥爪", "抓向", "掐住", "钳住", "袭来", "袭向",
    "拖走", "拽住", "砸向", "刺向", "劈向", "捅进", "捅向", "开枪", "射向",
    "挥拳", "挥刀", "命中", "打中",
)

_ENGAGE_CONFIRM_PROMPT = """\
你在核对一段刚刚生成的 TRPG 叙事：它有没有已经写出「打起来了」这个既成事实。
只看叙事文本本身，不要推测接下来会发生什么，也不要考虑「应不应该」开打。

先在叙事里找出**挨打的是谁**：这一下攻击落在谁身上、朝谁扑过去。把这个人填进 target。
engaged=true 仅当：叙事里已有一方**实际做出了会造成伤害的攻击动作**，且 target 是
party 里的玩家角色或队友、交锋已经开始——扑击、撕咬、挥击、开枪、抓住对方等，
怪物先动手或玩家先动手都算。

以下一律 false（从严）：
- 只是威胁、咆哮、逼近、盯住、缓慢靠近、堵住去路，还没真动手；
- 玩家只是在计划、提议、准备动手，或正屏息潜行、伺机而动；
- 攻击发生在回忆、假设、梦境、幻觉、比喻、他人转述的往事里；
- **挨打的不是 party 里的人**——两个 NPC 扭打成一团、怪物在啃早已死去的尸体，
  哪怕场面再激烈、party 就站在旁边看着，都是 false；
- 叙事只写了声响、动静、拖痕、血迹这些痕迹。

enemies 只填**这段叙事里确实动了手**的那一方，优先照抄 npcs_present 里的原名。
npcs_present 只是给你对名字用的花名册，**不是可以随手拉进来的参战名单**：叙事没写到的
NPC 一律不许填进 enemies，更不许替它编一个「它也被声音引来了」的理由。玩家角色和队友
绝不是敌方。trigger 用一句话说明因何打起来。

只输出 JSON：
{"engaged": true/false, "target": "挨打的人名", "enemies": ["名字"], "trigger": "一句话"}\
"""


def _is_party_member(target: str, party: list[str]) -> bool:
    """target 是否是玩家这一方的人。叙事里常用简称（「龙牙」之于「江户川龙牙」），故双向包含。"""
    target = (target or "").strip().casefold()
    if not target:
        return False
    for name in party:
        name = (name or "").strip().casefold()
        if name and (name in target or target in name):
            return True
    return False


async def _confirm_narrated_engagement(payload: dict) -> dict | None:
    """快模型二次确认：叙事是否真的已经进入交战。失败一律返回 None（宁可不开战）。"""
    from app.ai.llm_factory import get_fast_llm

    messages = [
        {"role": "system", "content": _ENGAGE_CONFIRM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    try:
        # 不设 max_tokens：推理模型的 reasoning 会把输出预算吃光，content 返回空串。
        raw = await get_fast_llm().complete(
            messages, temperature=0, response_format={"type": "json_object"},
        )
    except Exception:
        logger.exception("交战确认调用失败，本轮不补开战")
        return None
    data = turn_planner._extract_json_object(raw)
    if not isinstance(data, dict):
        logger.warning("交战确认输出无法解析为 JSON，本轮不补开战；原始片段：%s", str(raw)[:200])
        return None
    return data


async def _ensure_narrated_combat(
    db: Session,
    session_id: str,
    game_session: GameSession,
    module: Module,
    player_char: Character,
    teammates: list[Character] | None,
    llm,
    plan: turn_planner.TurnPlan | None,
    pre_gen_seq: int,
) -> AsyncIterator[str]:
    """叙事已经打起来、机制却没跟上 → 确定性补开战。

    与 ``_ensure_planned_combat`` 的分工：那道守卫兜的是「计划裁定了开战、KP 漏落地」，
    这道兜的是**计划自己判错**——怪物按 goals 该扑上来、KP 也照着写了它扑上来，但
    combat.should_start 仍是 false，于是交战只存在于文字里：没有先攻、没有回合，玩家
    没法还手。叙事与机制在这里脱节，比谁对谁错更伤——世界说它咬过来了，规则却当无事发生。

    判定分两段：免费词表预筛挡掉绝大多数轮次，命中才花一次快模型确认；确认从严，
    宁可漏判（维持现状）也不凭空开战——凭空开战会直接打断玩家正在进行的潜行/谈判。
    """
    if plan is not None and plan.combat.should_start:
        return  # 计划已裁定开战，交给 _ensure_planned_combat，别开两次

    from app.services import combat_service

    current = combat_service.get_combat(db.get(GameSession, session_id))
    if current and current.get("active"):
        return

    narration = _narrated_turn_text(db, session_id, pre_gen_seq)
    if not narration or not any(marker in narration for marker in _ENGAGE_MARKERS):
        return

    party = [player_char.name] + [t.name for t in (teammates or [])]
    verdict = await _confirm_narrated_engagement({
        "party": party,
        "npcs_present": _npcs_present(module, game_session),
        "player_input": _turn_evidence_text(db, session_id, pre_gen_seq)[:1500],
        "narration": narration[-3000:],
    })
    if not verdict or not verdict.get("engaged"):
        return

    # 确定性护栏：挨打的必须是玩家这一方。判定光靠 prompt 约束不住「两个 NPC 扭打、
    # party 在旁边看着」这种场面——实测它会判成开战，还顺手把叙事里根本没出场的怪物
    # 拉进来凑敌人。战斗轮是玩家的战斗轮，打不到玩家头上就不该起。
    target = str(verdict.get("target") or "").strip()
    if not _is_party_member(target, party):
        logger.info(
            "叙事判定已交战，但挨打的不是玩家一方（target=%s），不补开战: session=%s",
            target or "未给出", session_id,
        )
        return

    party_lower = {name.strip().casefold() for name in party if name.strip()}
    enemies = [
        str(name).strip() for name in (verdict.get("enemies") or [])
        if str(name).strip() and str(name).strip().casefold() not in party_lower
    ]
    if not enemies:
        # 判定说打起来了却给不出敌方，多半是它把玩家一方当成了「敌人」——凭空造一个
        # 杂兵比不开战更糟（玩家会被拉进一场跟谁打都不知道的战斗），直接放弃。
        logger.warning("叙事判定已交战但未给出敌方名字，本轮不补开战: session=%s", session_id)
        return

    trigger = str(verdict.get("trigger") or "").strip() or "交战在叙事中已经开始"
    logger.info(
        "叙事已交战但战斗态未起，守卫补开战: session=%s 敌方=%s 因由=%s",
        session_id, "，".join(enemies), trigger,
    )
    chunks = await _exec_start_combat(
        db, session_id, game_session, module, player_char, teammates, llm,
        "，".join(enemies), trigger,
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
    同一角色对同一恐怖源的去重仍由 _exec_san_check（session_ledger.san_checked）保证。
    """
    asked = plan is not None and plan.sanity.trigger
    plan = plan or turn_planner.TurnPlan()
    if not _sanity_has_evidence(
        db, session_id, game_session, module, player_char, plan, pre_gen_seq,
    ):
        # 只有「规划器确实要了 SAN、却拿不出恐怖证据」才值得报警——那是它在凭空加检定。
        # 规划器没要（trigger=False，含计划整个缺席时的空计划）是绝大多数回合的常态，
        # 这里曾经也打同一条 WARNING，读日志的人会以为守卫吞掉了一次该发的检定。
        if asked:
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

    幂等：按「本轮玩家行动锚序号 + 获/失 + 名字 + 角色」去重（存 turn_state.item_delta_keys），
    重新生成不会重复增减。物品效果仍由 KP 叙述——这里只保证库存数目可靠。

    **未决检定不预发收益**：本轮 requires_check=true 时，获得一律转入 turn_state.pending_item_gains
    暂存，等检定结算出来再由 `settle_pending_item_gains` 决定给还是不给。否则会出现「东西已经进包、
    才被要求投扒窃/撬锁」——掷输了那件东西算什么？失去/消耗不走这条：那多是尝试本身的代价
    （划掉最后一根火柴、绳子被割断），无论成败都已然发生。
    """
    # 新一轮开始就先作废上一轮遗留的暂存收益：玩家没投那颗骰子就又干别的去了，那份收益不该
    # 一直挂着、更不该被后面某次不相干的检定顺手兑现掉。放在所有早退之前，确保每轮都清。
    if _discard_pending_item_gains(db, game_session):
        yield _make_chunk("inventory_update")
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
    ws = dict(game_session.turn_state or {})
    done = set(ws.get("item_delta_keys") or [])
    changed = False

    def _who(name: str) -> Character:
        return _resolve_hp_target((name or "").strip(), player_char, teammates) or player_char

    # 本轮挂着未决检定 → 收获全部转暂存，等骰子落地再定给不给（见 settle_pending_item_gains）。
    deferred = []
    for ig in plan.items_gained if plan.requires_check else []:
        name = (ig.name or "").strip()
        if not name:
            continue
        target = _who(ig.who)
        key = f"g|{anchor}|{name}|{target.id}"
        if key in done:      # 重新生成时已兑现过的收益不再重复暂存
            continue
        deferred.append({
            "name": name, "qty": int(ig.qty or 1),
            "kind": (ig.kind or ""), "char_id": target.id, "key": key,
        })
    if deferred:
        ws["pending_item_gains"] = deferred
        changed = True

    for ig in ([] if plan.requires_check else plan.items_gained):
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
        game_session.turn_state = ws
        db.commit()


def _discard_pending_item_gains(db: Session, game_session: GameSession) -> bool:
    """作废暂存的待检定收益（检定失败 / 玩家干脆没投就又行动了）。返回是否真的清掉了东西。"""
    ws = dict(game_session.turn_state or {})
    if not ws.get("pending_item_gains"):
        return False
    ws["pending_item_gains"] = []
    game_session.turn_state = ws
    db.commit()
    return True


def settle_pending_item_gains(
    db: Session, session_id: str, game_session: GameSession, succeeded: bool,
) -> list[str]:
    """检定落地后结算暂存收益：成功才入库，失败一律作废。返回要广播的 chunk 列表。

    这是「先检定、后发货」的另一半——`_ensure_planned_items` 在有未决检定时只暂存不入库，
    真正决定给不给的是这里的骰子结果。玩家扒窃掷输了，那块表就不该在他包里。
    """
    ws = dict(game_session.turn_state or {})
    pending = list(ws.get("pending_item_gains") or [])
    if not pending:
        return []
    ws["pending_item_gains"] = []
    chunks: list[str] = []
    if succeeded:
        done = set(ws.get("item_delta_keys") or [])
        for item in pending:
            target = db.get(Character, str(item.get("char_id") or ""))
            name = str(item.get("name") or "").strip()
            key = str(item.get("key") or "")
            if target is None or not name or (key and key in done):
                continue
            qty = int(item.get("qty") or 1)
            inventory_service.add_item(
                db, target, name, qty=qty, kind=(str(item.get("kind") or "") or None),
            )
            if key:
                done.add(key)
            suffix = f"×{qty}" if qty > 1 else ""
            ev = session_service.add_event(
                db, session_id, "system", f"{target.name} 获得了 {name}{suffix}",
                actor_name="系统", metadata={"item_gain": True, "char_id": target.id},
            )
            chunks.append(
                _make_chunk("system", ev.content, event_id=ev.id, metadata={"item_gain": True}))
            chunks.append(_make_chunk("inventory_update", metadata={"char_id": target.id}))
        ws["item_delta_keys"] = list(done)
    else:
        chunks.append(_make_chunk("inventory_update"))
    game_session.turn_state = ws
    db.commit()
    return chunks


def settle_pending_clue_reveals(
    db: Session,
    session_id: str,
    game_session: GameSession,
    succeeded: bool,
    module: Module | None = None,
    on_first_clue=None,
) -> None:
    """检定落地后兑现暂存的线索候选：成功记入台账（known），失败丢弃。

    「先检定、后记账」——与 `settle_pending_item_gains` 同一范式。规划器在挂检定的那一轮
    只能写 reveal_level=none（写别的就是提前泄底），所以线索是否被掌握只有骰子知道。
    台账不是给玩家看的通知（线索内容由 KP 叙事给出），是喂给结局判定/卡关检测/复盘的账本，
    因此这里不广播 chunk，只在**首次入账**时补一张发现配图卡。
    """
    ws = dict(game_session.world_state or {})
    staged = ws.get("pending_clue_reveals") or {}
    ids = [str(c) for c in (staged.get("ids") or []) if str(c or "").strip()]
    ws.pop("pending_clue_reveals", None)
    before = set(ws.get("clue_ledger") or {})
    if ids and succeeded:
        ws = world_memory.record_clue_reveal(
            ws, ids, "direct", list(staged.get("discovered_by") or []),
            int(staged.get("seq") or 0), note=str(staged.get("note") or ""),
        )
    game_session.world_state = ws
    db.commit()
    if ids and succeeded and module is not None and on_first_clue:
        for cid in ids:
            if cid not in before:
                on_first_clue(db, session_id, module, cid)


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


#: 进场标记后面的宾语指到这些泛称之一时，说的就是本场景（「进入房间时全员聆听」）。
#: 精确相等才算——带修饰的宾语（「最里面的小屋」）恰恰说明特指场景**内部**的某处。
_ENTRY_GENERIC_PLACES = frozenset({
    "房间", "屋内", "室内", "内部", "此处", "该地", "这里", "现场", "场景", "本场景", "该场景",
})
#: 宾语的右边界：「进入X时」「到达X后」「进入X，」都在此截断。
_ENTRY_OBJECT_STOP = re.compile(r"[时后，,。；;：:、]")


def _entry_trigger_object(trigger: str) -> str | None:
    """进场标记后面跟的那个地点短语。

    无进场标记 → None；标记后没有宾语（「进入时」）→ 空串。
    """
    trigger = str(trigger or "")
    for marker in _ENTRY_CHECK_MARKERS:
        pos = trigger.find(marker)
        if pos < 0:
            continue
        tail = trigger[pos + len(marker):]
        stop = _ENTRY_OBJECT_STOP.search(tail)
        return (tail[:stop.start()] if stop else tail).strip()
    return None


def _is_scene_entry_trigger(trigger: str, scene: dict | None) -> bool:
    """这条 trigger 说的是不是「踏进**本场景**就触发」。

    光看有没有「进入」二字是不够的：『闇暗山』村庄遗址里那条
    ``trigger="进入最里面的小屋被拖拽"`` 说的是村内某间屋子，可它被当成了进村即触发——
    玩家刚踏进村口就被判了一次 STR，还把幂等键占掉，真进那间小屋时反而不再检定。
    所以要求宾语确实指向本场景：没有宾语（「进入时」）、泛称本场景（「进入房间时」），
    或与场景标题/关键词互相包含。指向场景内部某处的一律不算——那些等玩家真走过去，
    由 KP 按 events 明文自行裁定（现在它看得见每条机制点发生没有，见 ``format_scene_events_section``）。

    场景连个名字都没有时 fail-open 退回「有进场标记就算」：无从对文，宁可照旧发，
    也不要因为模组数据残缺就把既有机制整条漏掉。
    """
    obj = _entry_trigger_object(trigger)
    if obj is None:
        return False
    if not obj or obj in _ENTRY_GENERIC_PLACES:
        return True
    names = [(scene or {}).get("title"), (scene or {}).get("name")]
    names += list((scene or {}).get("keywords") or [])
    # 空白归一：模组里「7 号车厢」与 trigger 里的「7号车厢」是同一个地方。
    names = [n for n in (re.sub(r"\s+", "", str(n or "")) for n in names) if n]
    if not names:
        return True
    obj = re.sub(r"\s+", "", obj)
    return any(n in obj or obj in n for n in names)


def _scene_entry_check_mechanisms(
    module: Module | None, scene_id: str | None,
) -> list[tuple[int, dict]]:
    """该场景中「进入即触发」的技能检定机制点（模组明文，须带技能名）。

    与 ``_scene_sanity_mechanism`` 的 entry 分支同源，只是那边筛 san_check、这边筛 dice_check。
    不含进场标记的机制点（「搜寻丢失物品时」「悄悄通过时」）一律不在此列——那些要玩家先有行动；
    指向场景内部某处的（「进入最里面的小屋」）同样不在此列，判据见 ``_is_scene_entry_trigger``。
    """
    if not module or not scene_id:
        return []
    scene = next(
        (s for s in module.scenes or [] if str(s.get("id") or "") == str(scene_id)),
        None,
    )
    found: list[tuple[int, dict]] = []
    for index, event in enumerate((scene or {}).get("events", []) or []):
        if not isinstance(event, dict) or event.get("kind") != "dice_check":
            continue
        if not str(event.get("skill") or "").strip():
            continue
        if _is_scene_entry_trigger(str(event.get("trigger") or ""), scene):
            found.append((index, event))
    return found


def _entry_check_key(scene_id: str, index: int, char_id: str) -> str:
    """进场检定的幂等键：同一角色对同一机制点只检一次。"""
    return f"scene:{scene_id}:check:{index}:{char_id}"


async def _ensure_scene_entry_checks(
    db: Session,
    session_id: str,
    game_session: GameSession,
    module: Module | None,
    player_char: Character,
    teammates: list[Character] | None,
) -> AsyncIterator[str]:
    """模组明文的『进入场景即检定』机制点 → 后端确定性发出，每角色每机制点只发一次。

    补的是一个结构性缺口：这类机制常落在**开场那一刻**（起始场景就写着「进入时全员幸运」），
    而开场既不跑规划器（``turn_context`` 里 ``plan is None and events`` 为假），提示词又明令
    KP「不要触发任何检定指令」——两头一夹，整条机制此前只能默默漏掉。SAN 早有进场兜底
    （``_scene_sanity_mechanism`` 的 entry 分支），技能检定一直没有对等实现。

    判据只看「角色此刻在该场景、且这条机制对他还没检过」，不看本轮是否刚移动：分头行动的
    后到者、以及开场就站在起始场景里的全员，都能各自补上一次。
    """
    if module is None:
        return
    db.refresh(game_session)
    party = [player_char, *(teammates or [])]
    locations = {c.id: session_service.get_char_location(game_session, c.id) for c in party}
    done = set((game_session.world_state or {}).get(_ENTRY_CHECK_STATE_KEY) or [])
    for scene_id in dict.fromkeys(locations.values()):
        if not scene_id:
            continue
        here = [c for c in party if locations[c.id] == scene_id]
        for index, mechanism in _scene_entry_check_mechanisms(module, scene_id):
            targets = [c for c in here if _entry_check_key(scene_id, index, c.id) not in done]
            if not targets:
                continue
            kv = {
                "skill": str(mechanism.get("skill") or "").strip(),
                # 逐人点名而非「在场」：目标已按幂等键滤过，避免把检过的人再拉进来。
                "chars": "、".join(c.name for c in targets),
                "source": str(mechanism.get("trigger") or "").strip() or "场景机制",
            }
            try:
                chunks, _descs, _pending = await _exec_dice_check(
                    db, session_id, game_session, module, kv, player_char, teammates,
                )
            except Exception:
                logger.exception(
                    "进场检定补发失败（忽略本条）：session=%s scene=%s skill=%s",
                    session_id, scene_id, kv["skill"],
                )
                continue
            # 先记账再吐 chunk：_exec_dice_check 内部已改过 turn_state（pending_checks），
            # 必须重新取一次再合并写回，否则会用旧快照把待投检定覆盖掉。
            db.refresh(game_session)
            ws = dict(game_session.world_state or {})
            recorded = set(ws.get(_ENTRY_CHECK_STATE_KEY) or [])
            recorded.update(_entry_check_key(scene_id, index, c.id) for c in targets)
            ws[_ENTRY_CHECK_STATE_KEY] = sorted(recorded)
            # 同一件事记两本账：上面那本是后端私有的逐角色幂等键（拦重复发骰），
            # 这本进 KP 上下文（拦 KP 照着场景 events 明文把同一桥段重演一遍）。
            led = db.get(SessionLedger, game_session.id)
            if led is None:
                led = SessionLedger(session_id=game_session.id)
                db.add(led)
            led.scene_events_seen = world_memory.record_scene_event_seen(
                led.scene_events_seen, scene_id, index,
                session_service.get_next_sequence_num(db, session_id) - 1,
                note=kv["source"],
            )
            game_session.world_state = ws
            db.commit()
            done = recorded
            for chunk in chunks:
                yield chunk
