"""世界记忆层 v1：线索台账 + NPC 记忆（纯确定性来源，零额外 LLM 调用）。

把「玩家知道了什么」「NPC 记得什么」从上下文推断变成 world_state 里的持久化结构：

- ``clue_ledger``：只记录已被触碰的线索（未触碰 = 不在字典里），
  status 二态：partial（有所察觉）/ known（完全掌握），known 不降级。
- ``npc_memory``：每个 NPC 的态度 / 承诺 / 谎言 / 最近互动，
  interactions 是环形缓冲（最多 ``MAX_NPC_INTERACTIONS`` 条），防 world_state 无限膨胀。

本模块全部是「读-改-写 world_state dict」的纯函数：不触碰数据库、不修改入参，
返回一份更新后的拷贝，由调用方整 dict 回写 ``session.world_state``——SQLAlchemy 的
JSON 列就地修改不被追踪，必须整体重新赋值才会落库。
"""

from __future__ import annotations

# interactions 环形缓冲上限
MAX_NPC_INTERACTIONS = 8
# 台账备注 / 互动摘要的截断长度（确定性来源直接截原文，不调 LLM 浓缩）
NOTE_MAX_CHARS = 80

# TurnPlan.clue_policy.reveal_level → 台账状态：hint=有所察觉，direct=完全掌握。
# 规划器偶尔写出 full/known 等同义词，宽容归一；无法识别的非 none 值按 partial 保守处理
# （partial 可再升级为 known，不会错误地阻止后续完整揭示）。
_REVEAL_TO_STATUS = {
    "hint": "partial",
    "partial": "partial",
    "direct": "known",
    "full": "known",
    "known": "known",
}

_STATUS_LABEL = {"partial": "有所察觉", "known": "完全掌握"}
_ATTITUDE_LABEL = {
    "hostile": "敌视",
    "wary": "警惕",
    "neutral": "中立",
    "warming": "好感渐增",
    "trusting": "信任",
}


def _truncate(text, limit: int = NOTE_MAX_CHARS) -> str:
    text = str(text or "").strip().replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "…"


def reveal_status(reveal_level) -> str | None:
    """把 clue_policy.reveal_level 归一成台账状态；none/空 = 本轮不揭示，返回 None。"""
    lvl = str(reveal_level or "").strip().lower()
    if not lvl or lvl == "none":
        return None
    return _REVEAL_TO_STATUS.get(lvl, "partial")


def record_clue_reveal(
    ws: dict,
    clue_ids: list[str],
    reveal_level: str,
    discovered_by: list[str],
    seq: int,
    note: str = "",
) -> dict:
    """把一次线索揭示写入台账（partial ← hint，known ← direct）。

    known 不降级：已完全掌握的线索不会因为后续 hint 退回 partial；
    ``discovered_by`` 增量合并去重（分头行动下信息不共享，谁在场谁知道）；
    ``seq`` 只记首次触碰时的事件序号。
    """
    status = reveal_status(reveal_level)
    if status is None or not clue_ids:
        return ws
    ws = dict(ws or {})
    ledger = dict(ws.get("clue_ledger") or {})
    for cid in clue_ids:
        cid = str(cid or "").strip()
        if not cid:
            continue
        entry = dict(ledger.get(cid) or {})
        entry["status"] = "known" if entry.get("status") == "known" else status
        known_by = list(entry.get("discovered_by") or [])
        for who in discovered_by or []:
            if who and who not in known_by:
                known_by.append(who)
        entry["discovered_by"] = known_by
        entry["seq"] = entry.get("seq") or int(seq or 0)
        if note:
            entry["note"] = _truncate(note)
        ledger[cid] = entry
    ws["clue_ledger"] = ledger
    return ws


def stage_clue_reveal(
    ws: dict, clue_ids: list[str], discovered_by: list[str], seq: int, note: str = "",
) -> dict:
    """暂存「等这次检定落地才算数」的线索候选（world_state.pending_clue_reveals）。

    传空 ``clue_ids`` 即清空暂存。纯函数，返回更新后的拷贝。每轮只保留最新一批——
    玩家换了个行动，上一轮没掷的候选就不该再被下一次投骰兑现。
    """
    ws = dict(ws or {})
    ids = [str(c).strip() for c in (clue_ids or []) if str(c or "").strip()]
    if not ids:
        ws.pop("pending_clue_reveals", None)
        return ws
    ws["pending_clue_reveals"] = {
        "ids": ids,
        "discovered_by": list(discovered_by or []),
        "seq": int(seq or 0),
        "note": _truncate(note),
    }
    return ws


def handout_issued(ws: dict, handout_id: str) -> bool:
    """某手书是否已发放过（幂等判定的唯一真源：world_state.handouts_issued）。"""
    return str(handout_id or "") in set((ws or {}).get("handouts_issued") or [])


def blocked_scenes(ws: dict) -> dict:
    """当前被判定「过不去」的场景 → 原因。KP 用 [BLOCK_PATH] 标、[UNBLOCK_PATH] 解。

    ``connections`` 只说物理上相连，说不了「现在能不能过」——怪物堵着门、锁死的舱门、
    塌方，都是只有 KP 知道的故事事实。寻路据此**绕开**这些场景（但仍可把它们当终点：
    走进危险是玩家的自由，被拦住的是「借道穿过去」）。
    """
    raw = (ws or {}).get("blocked_scenes") or {}
    return {str(k): str(v or "") for k, v in raw.items() if k}


def record_block(ws: dict, scene_id: str, reason: str) -> dict:
    """标记某场景此刻过不去。纯函数；重复标记只更新原因。"""
    scene_id = str(scene_id or "").strip()
    if not scene_id:
        return dict(ws or {})
    out = dict(ws or {})
    blocked = dict(blocked_scenes(out))
    blocked[scene_id] = (reason or "").strip()
    out["blocked_scenes"] = blocked
    return out


def record_unblock(ws: dict, scene_id: str) -> dict:
    """解除阻断（威胁被清掉、门被打开）。纯函数；本来就没标记则 no-op。"""
    scene_id = str(scene_id or "").strip()
    out = dict(ws or {})
    blocked = dict(blocked_scenes(out))
    if blocked.pop(scene_id, None) is None:
        return out
    out["blocked_scenes"] = blocked
    return out


def scene_event_key(scene_id: str, index: int) -> str:
    """场景机制点的台账键：``场景 id:events 下标``。"""
    return f"{str(scene_id or '').strip()}:{int(index)}"


def scene_event_seen(seen: dict, scene_id: str, index: int) -> bool:
    """某场景机制点是否已经发生过（``scene_events_seen`` 是唯一真源）。

    ``seen`` 是台账本体（拆表后即 session_ledger.scene_events_seen），不再包 world_state。
    """
    key = scene_event_key(scene_id, index)
    return key in dict(seen or {})


def record_scene_event_seen(
    seen: dict, scene_id: str, index: int, seq: int, note: str = "",
) -> dict:
    """把「场景 events 里的这条机制点已经演过了」写进台账。纯函数，重复记只保留首次。

    模组场景的 ``events``（「进入最里面的小屋被拖拽」「阅读村规」）是**一次性桥段**，
    可它们随当前场景 JSON 每轮明文注入 KP 上下文，且此前没有任何「已经发生过」的标记——
    KP 于是照着同一份清单一演再演。实测『闇暗山』那局里「被手拖进屋」演了两轮，
    玩家当场在对话里回了一句「这不就是你刚才和我说的内容嘛」。

    后端确定性发出的进场检定此前只写 ``scene_entry_checks``——那是**后端私有的幂等键**，
    从不进上下文，拦得住系统重复发骰，拦不住 KP 重复叙事。本台账专供注入。
    """
    scene_id = str(scene_id or "").strip()
    if not scene_id:
        return dict(seen or {})
    out = dict(seen or {})
    key = scene_event_key(scene_id, index)
    if key not in out:
        out[key] = {"seq": int(seq or 0), "note": _truncate(note)}
    return out


def travel_suggested(ws: dict, scene_id: str) -> bool:
    """某地点是否已经给玩家挂过「要不要去」的建议卡（幂等真源）。"""
    return str(scene_id or "") in set((ws or {}).get("travel_suggested") or [])


def record_travel_suggestion(ws: dict, scene_id: str) -> dict:
    """记下「已经问过要不要去某处」。纯函数，重复记为 no-op。

    去重按整局而非按回合：同一个地方反复弹卡是最烦人的形态，而玩家真想去时大地图一直都在，
    问过一次就够了。
    """
    scene_id = str(scene_id or "").strip()
    if not scene_id:
        return dict(ws or {})
    out = dict(ws or {})
    suggested = list(out.get("travel_suggested") or [])
    if scene_id not in suggested:
        suggested.append(scene_id)
    out["travel_suggested"] = suggested
    return out


def record_handout_issue(
    ws: dict,
    handout_id: str,
    title: str,
    discovered_by: list[str],
    seq: int,
) -> dict:
    """把一次手书发放写入世界记忆：handouts_issued（幂等真源）+ clue_ledger（status=known，
    kind 标注 handout——发放即全文可见，玩家「完全掌握」，经台账自然进入 KP 上下文）。

    纯函数，与 ``record_clue_reveal`` 同风格：不改入参、返回更新后的拷贝；重复发放为 no-op。
    """
    handout_id = str(handout_id or "").strip()
    if not handout_id:
        return ws
    ws = dict(ws or {})
    issued = list(ws.get("handouts_issued") or [])
    if handout_id not in issued:
        issued.append(handout_id)
    ws["handouts_issued"] = issued
    ledger = dict(ws.get("clue_ledger") or {})
    entry = dict(ledger.get(handout_id) or {})
    entry["status"] = "known"
    entry["kind"] = "handout"
    known_by = list(entry.get("discovered_by") or [])
    for who in discovered_by or []:
        if who and who not in known_by:
            known_by.append(who)
    entry["discovered_by"] = known_by
    entry["seq"] = entry.get("seq") or int(seq or 0)
    if title:
        entry["note"] = _truncate(f"手书《{title}》已发放，全桌可见全文")
    ledger[handout_id] = entry
    ws["clue_ledger"] = ledger
    return ws


# 代词/指人虚词，不能作 NPC 名（台词归属的后置代词兜底会产出「她」「他」等）。
_PRONOUN_NPC_NAMES = {
    "她", "他", "它", "您", "咱", "我", "你", "其", "这", "那",
    "我们", "你们", "他们", "她们", "它们", "咱们",
}
# 明显不属于人名/称呼的特征字：虚词/连词/副词/进行体/句末语气/结构指称/说话动词——
# 命中即判定为「旁白碎片被误当说话人」（如「修女在回」「但字距稍疏」「第七节」）。
_NON_NAME_CHARS = "在正又也但却而则就还并虽第节章页条款幕说道问答喊叫笑声开口的了着过吗呢吧啊呀嘛，,。！？"


def is_plausible_npc_name(name: str) -> bool:
    """粗判是否像一个 NPC 人名/称呼（护士长、前台女士、玛格丽特修女…）。

    用于挡掉台词归属启发式的误命中——旁白碎片、代词、动词短语、结构指称被登记/展示为
    临场 NPC（如「修女在回」「她」「但字距稍疏」「第七节」）。宁可漏掉个别真龙套（顶多不进
    可收编名单），也不让垃圾名污染临场角色列表与注入 KP 的名单。
    """
    name = (name or "").strip()
    if not (2 <= len(name) <= 6):
        return False
    if name in _PRONOUN_NPC_NAMES:
        return False
    return not any(c in name for c in _NON_NAME_CHARS)


def record_improvised_npc(ws: dict, name: str, seq: int) -> dict:
    """登记一个「临场 NPC」（模组未列出、KP 临时添加的开口龙套）到 world_state.improvised_npcs。

    只增不删；``mentions`` 每登记一次自增，用于观察存在感（不驱动任何自动行为）。
    key 用规整后的显示名（同名变体不做模糊合并，见设计文档 §7）。
    不像人名/称呼的（台词归属误命中的旁白碎片/代词/动词短语）直接丢弃，不登记。
    """
    name = str(name or "").strip()
    if not name or not is_plausible_npc_name(name):
        return ws
    ws = dict(ws or {})
    improv = dict(ws.get("improvised_npcs") or {})
    entry = dict(improv.get(name) or {})
    entry["first_seq"] = entry.get("first_seq", int(seq or 0))
    entry["last_seq"] = int(seq or 0)
    entry["mentions"] = int(entry.get("mentions", 0)) + 1
    improv[name] = entry
    ws["improvised_npcs"] = improv
    return ws


def promote_improvised_npc(ws: dict, name: str, card: dict) -> dict:
    """把一张转正 NPC 卡挂到 improvised_npcs[name].card（会话级，不写模组本体）。

    card 需含 name/description/personality/background/secrets；此处补一个 `improv_` 前缀的
    稳定 id（供 npc_memory / [NPC_ACT] 归属）。name 必须已登记在 improvised_npcs 里。
    """
    name = str(name or "").strip()
    if not name or not isinstance(card, dict):
        return ws
    ws = dict(ws or {})
    improv = dict(ws.get("improvised_npcs") or {})
    entry = dict(improv.get(name) or {})
    card = dict(card)
    # 稳定 id：优先复用已有卡的 id，否则据首次登场序号派生（同名不同局互不影响）
    card_id = str((entry.get("card") or {}).get("id") or "").strip()
    if not card_id:
        card_id = f"improv_{entry.get('first_seq', 0)}_{abs(hash(name)) % 100000}"
    card["id"] = card_id
    card.setdefault("secrets", [])
    entry["card"] = card
    improv[name] = entry
    ws["improvised_npcs"] = improv
    return ws


def promoted_npc_cards(ws: dict) -> list[dict]:
    """返回本会话已转正的临场 NPC 卡列表（module.npcs 同形，带 improvised 标记）。

    未转正（无 card）的临场龙套不返回——他们仍受「临场角色纪律」约束、不入正典集合。
    """
    improv = (ws or {}).get("improvised_npcs") or {}
    out: list[dict] = []
    for name, entry in improv.items():
        card = (entry or {}).get("card") if isinstance(entry, dict) else None
        if isinstance(card, dict) and card.get("id") and card.get("name"):
            out.append({**card, "improvised": True})
    return out


def record_npc_interaction(ws: dict, npc_id: str, seq: int, summary: str) -> dict:
    """给某 NPC 的互动史追加一条（环形缓冲，只保留最近 ``MAX_NPC_INTERACTIONS`` 条）。"""
    npc_id = str(npc_id or "").strip()
    summary = _truncate(summary)
    if not npc_id or not summary:
        return ws
    ws = dict(ws or {})
    memory = dict(ws.get("npc_memory") or {})
    entry = dict(memory.get(npc_id) or {})
    interactions = list(entry.get("interactions") or [])
    interactions.append({"seq": int(seq or 0), "summary": summary})
    entry["interactions"] = interactions[-MAX_NPC_INTERACTIONS:]
    memory[npc_id] = entry
    ws["npc_memory"] = memory
    return ws


# 抽取器允许写入的 NPC 态度枚举——超出集合的值视为幻觉，丢弃不写。
_VALID_ATTITUDES = set(_ATTITUDE_LABEL)


def _append_unique(existing, additions) -> list[str]:
    """把 ``additions`` 里的非空文本追加进 ``existing`` 列表，去重且保留原顺序。"""
    out = [str(p) for p in (existing or []) if str(p).strip()]
    seen = set(out)
    for item in additions or []:
        item = str(item or "").strip()
        if item and item not in seen:
            out.append(item)
            seen.add(item)
    return out


def apply_memory_delta(
    ws: dict,
    npc_updates: dict | None = None,
    clue_notes: dict | None = None,
) -> dict:
    """把 MemoryKeeper 抽取器产出的差量合并进 world_state（v2，低温兜底）。

    安全边界（防抽取器幻觉污染确定性台账）：
    - **只允许改已存在的 NPC**：``npc_updates`` 里不在 ``npc_memory`` 的 key 一律忽略——
      NPC 记忆的「诞生」仍由 v1 确定性钩子（说话/被看穿）负责，抽取器只做增量修饰。
    - 每个 NPC 只允许改 ``attitude``（须落在枚举内）/ ``attitude_reason`` /
      追加 ``new_promises`` 到 ``promises`` / 追加 ``new_lies`` 到 ``lies_told``；
      追加均去重保序，绝不触碰 ``interactions`` 环形缓冲。
    - **严禁改 ``clue_ledger`` 的 status**：``clue_notes`` 只允许更新**已存在**线索条目的
      ``note`` 备注，绝不新建条目、绝不改状态——玩家是否已知一律以 v1 确定性来源为准。

    纯函数：不改入参，返回更新后的新 dict；无有效差量则原样返回（保持原记忆不变）。
    """
    ws = dict(ws or {})
    if npc_updates:
        memory = dict(ws.get("npc_memory") or {})
        changed = False
        for nid, upd in npc_updates.items():
            nid = str(nid or "").strip()
            # 只修饰已存在的 NPC：抽取器不得凭空造出未被玩家触碰过的 NPC 记忆
            if not nid or nid not in memory or not isinstance(upd, dict):
                continue
            entry = dict(memory[nid])
            attitude = str(upd.get("attitude") or "").strip().lower()
            if attitude in _VALID_ATTITUDES:
                entry["attitude"] = attitude
                reason = str(upd.get("attitude_reason") or "").strip()
                if reason:
                    entry["attitude_reason"] = _truncate(reason)
            entry["promises"] = _append_unique(
                entry.get("promises"), upd.get("new_promises"),
            )
            entry["lies_told"] = _append_unique(
                entry.get("lies_told"), upd.get("new_lies"),
            )
            memory[nid] = entry
            changed = True
        if changed:
            ws["npc_memory"] = memory
    if clue_notes:
        ledger = dict(ws.get("clue_ledger") or {})
        changed = False
        for cid, note in clue_notes.items():
            cid = str(cid or "").strip()
            note = _truncate(note)
            # 只更新已存在线索的备注：不新建条目、不碰 status（是否已知以确定性来源为准）
            if not cid or cid not in ledger or not note:
                continue
            entry = dict(ledger[cid])
            entry["note"] = note
            ledger[cid] = entry
            changed = True
        if changed:
            ws["clue_ledger"] = ledger
    return ws


# ---------------------------------------------------------------------------
# AI 队友私有记忆（team_memory）：goals 个人目标 / notes 心里记着的事 / deeds 自己的言行。
# deeds 由确定性钩子写（队友每次行动落一条，零 LLM）；goals/notes 由 MemoryKeeper 抽取器
# 差量维护（apply_team_memory_delta，带安全边界）。只注入该队友自己的上下文，不进玩家视野。
# ---------------------------------------------------------------------------

# deeds 环形缓冲上限：覆盖远超事件摘要窗口（30 条混合事件）的自身行动史
MAX_TEAM_DEEDS = 10
# goals 上限：满了丢最旧（旧目标要么已完成被 done_goals 移除，要么早已过时）
MAX_TEAM_GOALS = 6
# notes 环形缓冲上限
MAX_TEAM_NOTES = 8


def team_memory_of(ws: dict, char_id: str) -> dict:
    return dict(dict((ws or {}).get("team_memory") or {}).get(str(char_id)) or {})


def record_team_deed(ws: dict, char_id: str, seq: int, summary: str) -> dict:
    """给某 AI 队友的言行史追加一条（环形缓冲，只保留最近 ``MAX_TEAM_DEEDS`` 条）。"""
    char_id = str(char_id or "").strip()
    summary = _truncate(summary)
    if not char_id or not summary:
        return ws
    ws = dict(ws or {})
    memory = dict(ws.get("team_memory") or {})
    entry = dict(memory.get(char_id) or {})
    deeds = list(entry.get("deeds") or [])
    deeds.append({"seq": int(seq or 0), "summary": summary})
    entry["deeds"] = deeds[-MAX_TEAM_DEEDS:]
    memory[char_id] = entry
    ws["team_memory"] = memory
    return ws


def apply_team_memory_delta(ws: dict, team_updates: dict | None, allowed_ids) -> dict:
    """把 MemoryKeeper 抽取的队友记忆差量合并进 world_state。

    安全边界（防抽取器幻觉污染）：
    - key 必须落在 ``allowed_ids``（本会话真实存在的 AI 队友 id）内，其余一律忽略；
    - 每个队友只允许：追加 ``new_goals`` 到 goals（去重，超上限丢最旧）、
      按精确文本用 ``done_goals`` 移除已完成目标、追加 ``new_notes`` 到 notes
      （去重，环形上限）；绝不触碰 ``deeds``（那是确定性来源）。

    纯函数：不改入参，返回更新后的新 dict；无有效差量则原样返回。
    """
    if not team_updates:
        return ws
    allowed = {str(i) for i in (allowed_ids or ())}
    ws = dict(ws or {})
    memory = dict(ws.get("team_memory") or {})
    changed = False
    for cid, upd in team_updates.items():
        cid = str(cid or "").strip()
        if not cid or cid not in allowed or not isinstance(upd, dict):
            continue
        entry = dict(memory.get(cid) or {})
        goals = _append_unique(entry.get("goals"), upd.get("new_goals"))
        done = {str(g or "").strip() for g in (upd.get("done_goals") or [])}
        if done:
            goals = [g for g in goals if g not in done]
        entry["goals"] = goals[-MAX_TEAM_GOALS:]
        entry["notes"] = _append_unique(
            entry.get("notes"), upd.get("new_notes"),
        )[-MAX_TEAM_NOTES:]
        memory[cid] = entry
        changed = True
    if changed:
        ws["team_memory"] = memory
    return ws


def format_team_self_memory(ws: dict, char_id: str) -> str:
    """给 ``build_team_context`` 的「你的私人记忆」小节：该队友自己的目标/心事/言行史。

    无任何记忆返回空串（不注入，向后兼容）。
    """
    mem = team_memory_of(ws, char_id)
    if not mem:
        return ""
    lines = [
        "【你的私人记忆】（只有你自己知道；让它影响你的判断和情绪，但不必急于完成目标；"
        "若当下感受、关系或风险与目标冲突，优先按角色当下的真实反应行事）",
    ]
    goals = [str(g) for g in (mem.get("goals") or []) if str(g).strip()]
    if goals:
        lines.append("你当前的个人目标：" + "；".join(goals))
    notes = [str(n) for n in (mem.get("notes") or []) if str(n).strip()]
    if notes:
        lines.append("你记在心里的事：" + "；".join(notes))
    deeds = [
        str((d or {}).get("summary") or "").strip()
        for d in (mem.get("deeds") or [])
    ]
    deeds = [d for d in deeds if d]
    if deeds:
        lines.append("你此前的言行（从旧到新）：" + "；".join(deeds))
    return "\n".join(lines) if len(lines) > 1 else ""


def format_team_memory_all_brief(ws: dict, id_to_name: dict[str, str]) -> str:
    """把**全部在场 AI 队友**的私有记忆各渲一行，喂给 MemoryKeeper 抽取器当输入。

    与 NPC 版不同：按传入的队友清单遍历（而非只遍历已有记忆的 key）——没有记忆的队友
    也要列出，否则抽取器永远没法为其建立第一个目标（差量 key 必须落在已列出的 id 内）。
    无队友返回空串。
    """
    if not id_to_name:
        return ""
    lines: list[str] = []
    for cid, name in id_to_name.items():
        mem = team_memory_of(ws, cid)
        parts: list[str] = []
        goals = [str(g) for g in (mem.get("goals") or []) if str(g).strip()]
        if goals:
            parts.append("目标：" + "；".join(goals))
        notes = [str(n) for n in (mem.get("notes") or []) if str(n).strip()]
        if notes:
            parts.append("心事：" + "；".join(notes))
        brief = "。".join(parts) or "（暂无个人目标与心事）"
        lines.append(f"- {cid}（{name}）：{brief}")
    return "\n".join(lines)


def advance_backstage_cursor(ws: dict, seq: int, scene_id: str | None = None) -> dict:
    """推进幕后推演游标（``world_state.backstage``，设计稿 1.1 预留的子键）。

    ``last_run_seq``：下次推演从此事件序号之后起算「玩家回合」；
    ``last_scene_id``：上次推演时所在场景——之后发生 [SCENE_CHANGE] 即触发下一次推演。
    纯函数：不改入参，返回更新后的新 dict。只动 backstage 子键，绝不触碰
    flags / clue_ledger 等剧情状态（幕后推演的安全约束）。
    """
    ws = dict(ws or {})
    bs = dict(ws.get("backstage") or {})
    bs["last_run_seq"] = int(seq or 0)
    if scene_id:
        bs["last_scene_id"] = scene_id
    ws["backstage"] = bs
    return ws


def discovered_clue_status(ws: dict) -> dict[str, str]:
    """{clue_id: status}，只含已被触碰的线索——给 planner 做 candidate 过滤输入。"""
    out: dict[str, str] = {}
    for cid, entry in dict((ws or {}).get("clue_ledger") or {}).items():
        status = (entry or {}).get("status")
        if status in ("partial", "known"):
            out[str(cid)] = status
    return out


def npc_memory_of(ws: dict, npc_id: str) -> dict:
    return dict(dict((ws or {}).get("npc_memory") or {}).get(str(npc_id)) or {})


def format_clue_ledger_section(
    ws: dict,
    clue_names: dict[str, str] | None = None,
    char_names: dict[str, str] | None = None,
    visible_clues: list[dict] | None = None,
) -> str:
    """渲染 KP 上下文的「线索台账」小节。

    ``visible_clues`` 给定时**全量对账**：把这批线索连同「尚未给出」的一并列出，逐条标状态。
    这是刻意的——原先只列已触碰的线索、台账空就整段不注入，等于**失效时完全静默**：
    『闇暗山』那局跑了 6 个场景、44 次掷骰，台账一条没有（记账全靠规划器自觉填 clue_id），
    于是「不要重复安排发现桥段」这句硬指示从头到尾没进过上下文，KP 对着线索明文重演。
    全量列出后，即便记账链路整条失灵，这一小节仍在，KP 至少看得见清单本身。

    只列 ``visible_clues`` 不扩大泄露面：这批就是「场景列表/线索」小节已经给过 KP 的同一批
    （同一个 ``visible_scene_ids`` 过滤，见 ``context._compact_clues``）。台账里另有条目
    （手书、已离开场景的线索）一律照列——那些玩家早就拿到了，无密可守。

    两者皆空时返回空串（向后兼容，不注入）。
    """
    ledger = dict((ws or {}).get("clue_ledger") or {})
    clue_names = dict(clue_names or {})
    char_names = char_names or {}
    pending: list[str] = []
    for clue in visible_clues or []:
        cid = str((clue or {}).get("id") or "").strip()
        if not cid:
            continue
        clue_names.setdefault(cid, str(clue.get("name") or ""))
        if cid not in ledger:
            pending.append(cid)
    if not ledger and not pending:
        return ""

    def _head(cid: str) -> str:
        name = clue_names.get(cid)
        return f"{name}（{cid}）" if name else cid

    lines = ["【线索台账】（内部资料——玩家已掌握的线索进度，绝不向玩家复述本清单或线索 id）"]
    for cid in sorted(ledger):
        entry = ledger[cid] or {}
        label = _STATUS_LABEL.get(entry.get("status"), str(entry.get("status") or ""))
        who = "、".join(char_names.get(w, w) for w in (entry.get("discovered_by") or []))
        line = f"- {_head(cid)}：{label}" + (f"｜知晓者：{who}" if who else "")
        if entry.get("note"):
            line += f"｜备注：{entry['note']}"
        lines.append(line)
    for cid in sorted(pending):
        lines.append(f"- {_head(cid)}：尚未给出")
    lines.append(
        "标「完全掌握」的线索玩家已经拿到手，绝不要再安排一次「发现」桥段，也不要当成新信息"
        "重新揭示；标「有所察觉」的玩家只摸到边角，可以让他们查得更深，但已经讲过的部分不要"
        "重讲一遍；标「尚未给出」的玩家还不知道，照常守密，只在他们真调查到时才揭示。"
    )
    return "\n".join(lines)


def format_scene_events_section(seen: dict, scene: dict | None) -> str:
    """渲染当前场景的「机制点进度」小节：events 逐条标已发生/未发生。

    ``seen`` 是台账本体（session_ledger.scene_events_seen），不再包 world_state。

    当前场景整份 JSON（含 ``events``）每轮明文注入 KP 上下文，而此前没有任何一条带着
    「这个桥段已经演过了」的标记——KP 便照单重演（详见 ``record_scene_event_seen``）。
    无 events 的场景返回空串（不注入）。
    """
    events = [e for e in ((scene or {}).get("events") or []) if isinstance(e, dict)]
    if not events:
        return ""
    scene_id = str((scene or {}).get("id") or "").strip()
    lines = ["【本场景机制点进度】（内部资料，绝不向玩家复述本清单）"]
    for index, event in enumerate(events):
        trigger = str(event.get("trigger") or "").strip() or f"机制点 {index + 1}"
        is_seen = scene_event_seen(seen, scene_id, index)
        lines.append(f"- {trigger}：{'已发生' if is_seen else '尚未发生'}")
    lines.append(
        "标「已发生」的桥段本局已经演过，绝不要重演、也不要换个说法再来一次；"
        "标「尚未发生」的按模组写明的触发条件照常裁定。"
    )
    return "\n".join(lines)


def format_npc_memory_brief(ws: dict, npc_id: str) -> str:
    """单个 NPC 记忆的一行摘要（态度/承诺/谎言/最近互动），无记忆返回空串。"""
    mem = npc_memory_of(ws, npc_id)
    if not mem:
        return ""
    parts: list[str] = []
    attitude = str(mem.get("attitude") or "").strip()
    if attitude:
        reason = str(mem.get("attitude_reason") or "").strip()
        parts.append(
            f"态度：{_ATTITUDE_LABEL.get(attitude, attitude)}"
            + (f"（{reason}）" if reason else "")
        )
    promises = [str(p) for p in (mem.get("promises") or []) if str(p).strip()]
    if promises:
        parts.append("承诺过：" + "；".join(promises))
    lies = [str(p) for p in (mem.get("lies_told") or []) if str(p).strip()]
    if lies:
        parts.append("说过的谎：" + "；".join(lies))
    recent = [
        str((i or {}).get("summary") or "").strip()
        for i in (mem.get("interactions") or [])[-3:]
    ]
    recent = [r for r in recent if r]
    if recent:
        parts.append("最近互动：" + "；".join(recent))
    return "。".join(parts)


def format_npc_memory_all_brief(ws: dict, npc_names: dict[str, str] | None = None) -> str:
    """把 npc_memory 里所有 NPC 的记忆各渲一行，喂给 MemoryKeeper 抽取器当输入。

    与需要 npc_defs 的 ``format_npc_memory_section`` 不同：抽取点（滚动摘要处）手边未必有
    module，这里直接遍历记忆字典，行首用 npc_id（抽取器差量的 key 必须是 id），可选带上名字。
    无记忆返回空串。
    """
    memory = dict((ws or {}).get("npc_memory") or {})
    if not memory:
        return ""
    names = npc_names or {}
    lines: list[str] = []
    for nid in sorted(memory):
        brief = format_npc_memory_brief(ws, nid)
        name = names.get(nid)
        head = f"{nid}（{name}）" if name else nid
        lines.append(f"- {head}：{brief}" if brief else f"- {head}：（暂无记忆细节）")
    return "\n".join(lines)


def format_npc_memory_section(ws: dict, npc_defs: list[dict] | None) -> str:
    """KP 上下文的「NPC 记忆」小节：只列有记忆的 NPC（有记忆＝已被玩家触碰过）。"""
    memory = dict((ws or {}).get("npc_memory") or {})
    if not memory:
        return ""
    lines: list[str] = []
    for npc in npc_defs or []:
        nid = npc.get("id")
        if not nid or nid not in memory:
            continue
        brief = format_npc_memory_brief(ws, nid)
        if brief:
            lines.append(f"- {npc.get('name') or nid}：{brief}")
    if not lines:
        return ""
    return (
        "【NPC 记忆】（各 NPC 记得的过往——他们记得对玩家的承诺与自己说过的谎，"
        "其言行必须与之一致，不得凭空遗忘或自相矛盾）\n" + "\n".join(lines)
    )


def format_npc_self_memory(ws: dict, npc_id: str) -> str:
    """给 ``build_npc_context`` 的「你的记忆」小节：该 NPC 自己的记忆全量注入。"""
    mem = npc_memory_of(ws, npc_id)
    if not mem:
        return ""
    lines = ["【你的记忆】（你清楚地记得下面这些事，言行必须与之一致）"]
    attitude = str(mem.get("attitude") or "").strip()
    if attitude:
        reason = str(mem.get("attitude_reason") or "").strip()
        lines.append(
            f"你对这队调查者的态度：{_ATTITUDE_LABEL.get(attitude, attitude)}"
            + (f"——{reason}" if reason else "")
        )
    promises = [str(p) for p in (mem.get("promises") or []) if str(p).strip()]
    if promises:
        lines.append("你许下过的承诺（要记得兑现或找借口拖延）：" + "；".join(promises))
    lies = [str(p) for p in (mem.get("lies_told") or []) if str(p).strip()]
    if lies:
        lines.append("你说过的谎（不要自相矛盾，也不要轻易承认）：" + "；".join(lies))
    interactions = [
        str((i or {}).get("summary") or "").strip()
        for i in (mem.get("interactions") or [])
    ]
    interactions = [i for i in interactions if i]
    if interactions:
        lines.append("最近与你有关的事（从旧到新）：" + "；".join(interactions))
    return "\n".join(lines) if len(lines) > 1 else ""


# ── 滚动剧情摘要的分层章节（LSM 式）──────────────────────────────────────
#
# 早先的做法是「既往摘要 + 新事件 → 全量重写」：第 N 次浓缩建立在第 N-1 次的产物上，
# 误差**复利累积**，且每次重写都可能把仍然重要的旧内容挤掉。长局跑几十轮之后，开场的
# 细节几乎必然模糊。
#
# 改成追加式章节后：每次浓缩只产出覆盖**本批新事件**的一章，直接追加；只有章节数攒够了
# 才把最老的几章做一次二次合并。于是近期章节只被压过一次、保真度高，误差不再对整份摘要
# 复利，只在被反复合并的老章节上累积——而那部分正好可以用 recall_history 把原文捞回来。
#
# 每章带 seq 区间，与 event_recall 的回捞联动：KP 知道「第三章覆盖 seq 100-124」，
# 要细节时按那段去查。
STORY_CHAPTERS_KEY = "story_chapters"
#: 章节数超过这个数就把最老的一批合并成一章，避免章节无限增长。
MAX_STORY_CHAPTERS = 6
#: 每次二次合并吃掉几章。取 3：合并后仍留 4 章，不会一下子把中段历史压成一团。
MERGE_CHAPTER_BATCH = 3


def story_chapters(ws: dict) -> list[dict]:
    """取分层章节列表（兼容旧存档：单串 story_summary 视作第一章）。"""
    chapters = (ws or {}).get(STORY_CHAPTERS_KEY)
    if isinstance(chapters, list) and chapters:
        return [c for c in chapters if isinstance(c, dict) and (c.get("text") or "").strip()]
    legacy = ((ws or {}).get("story_summary") or "").strip()
    if legacy:
        return [{"text": legacy, "from_seq": 0, "to_seq": (ws or {}).get("story_summary_seq") or 0}]
    return []


def story_summary_text(ws: dict) -> str:
    """把各章按顺序拼成给 KP 看的完整剧情梗概。

    只有一章时不加小标题——单章加标题反而像「文档」而不是前情提要。
    """
    chapters = story_chapters(ws)
    if not chapters:
        return ""
    if len(chapters) == 1:
        return chapters[0]["text"].strip()
    return "\n\n".join(
        f"【第 {i} 章】{c['text'].strip()}" for i, c in enumerate(chapters, start=1)
    )


def append_story_chapter(ws: dict, text: str, from_seq: int, to_seq: int) -> dict:
    """追加一章（纯函数，返回新 ws）。空文本原样返回。

    同时保留 ``story_summary`` 的拼接快照：前端「上下文占用」与回放等旧读取口径仍看它，
    换成章节制不该要求它们同步改。
    """
    text = (text or "").strip()
    if not text:
        return ws
    chapters = list(story_chapters(ws))
    chapters.append({"text": text, "from_seq": int(from_seq), "to_seq": int(to_seq)})
    new_ws = dict(ws or {})
    new_ws[STORY_CHAPTERS_KEY] = chapters
    new_ws["story_summary"] = story_summary_text(new_ws)
    return new_ws


def chapters_to_merge(ws: dict) -> list[dict]:
    """章节数超限时返回该被二次合并的最老几章；否则空列表。"""
    chapters = story_chapters(ws)
    if len(chapters) <= MAX_STORY_CHAPTERS:
        return []
    return chapters[:MERGE_CHAPTER_BATCH]


def replace_merged_chapters(ws: dict, merged_text: str, count: int) -> dict:
    """用一段合并结果替换最老的 ``count`` 章（纯函数）。合并失败/空文本时原样返回。"""
    merged_text = (merged_text or "").strip()
    chapters = story_chapters(ws)
    if not merged_text or count <= 0 or len(chapters) < count:
        return ws
    head, tail = chapters[:count], chapters[count:]
    merged = {
        "text": merged_text,
        "from_seq": head[0].get("from_seq", 0),
        "to_seq": head[-1].get("to_seq", 0),
    }
    new_ws = dict(ws or {})
    new_ws[STORY_CHAPTERS_KEY] = [merged, *tail]
    new_ws["story_summary"] = story_summary_text(new_ws)
    return new_ws
