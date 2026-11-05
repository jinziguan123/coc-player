"""模组 v3「事件—条件—效果」内核。

这个模块只做纯计算：把「剧情事件」表示成可判定的条件 + 可执行的效果，
并提供三样东西给上层复用——

1. evaluate_condition：条件表达式的三值求值（True / False / None=判不了）；
2. validate_module：整份模组的引用完整性与闭环校验（断链、悬空 flag、不可达）；
3. normalize_events：把旧格式（scene.events 的 kind/note）规整成 v3 事件。

**边界**：不碰数据库、不碰网络、不调用 LLM。执行效果（真实搬人、扣 SAN）
在 services/beat_runtime.py；这里只回答「会不会发生」「结构对不对」。

设计文档见 docs/adr/ADR-009-模组事件与效果模型.md。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

#: 当前解析产出与运行时理解的模组结构版本。
SCHEMA_VERSION = 3

#: 条件原子：键即原子名，值为字符串参数。
CONDITION_ATOMS: tuple[str, ...] = (
    "flag", "at", "visited", "clue_known", "has_item", "phase",
)

#: 效果类型全集。每个类型都必须有执行器（或明确归入 narration 由 KP 演出）。
EFFECT_TYPES: tuple[str, ...] = (
    "check",
    "sanity",
    "damage",
    "heal",
    "goto",
    "set_flag",
    "clear_flag",
    "reveal_clue",
    "grant_item",
    "remove_item",
    "npc_state",
    "unlock",
    "block",
    "ending",
    "narration",
)

#: 每种效果的必填参数（校验用）。
_EFFECT_REQUIRED: dict[str, tuple[str, ...]] = {
    "check": ("skill",),
    "sanity": ("loss",),
    "damage": ("dice",),
    "heal": ("amount",),
    "goto": ("target",),
    "set_flag": ("flag",),
    "clear_flag": ("flag",),
    "reveal_clue": ("clue_id",),
    "grant_item": ("item",),
    "remove_item": ("item",),
    "npc_state": ("npc_id",),
    "unlock": ("location_id",),
    "block": ("location_id",),
    "ending": ("ending_id",),
    "narration": ("text",),
}

#: check 的子效果分支字段。
_BRANCH_KEYS: tuple[str, ...] = ("on_success", "on_failure")


@dataclass(frozen=True)
class FlowState:
    """求值条件时需要的世界状态快照（只读）。"""

    flags: dict[str, Any] = field(default_factory=dict)
    at: str | None = None
    visited: frozenset[str] = frozenset()
    clues: frozenset[str] = frozenset()
    items: dict[str, Any] = field(default_factory=dict)
    phase: str = "scene"
    seen: frozenset[str] = frozenset()


# ── 条件求值 ──────────────────────────────────────────────────────────────


def evaluate_condition(expr: Any, state: FlowState) -> bool | None:
    """三值求值：True 满足 / False 不满足 / None 判不了（缺数据或表达式不合法）。

    None 不是失败——调用方据此把事件标成「需模型判断」，而不是假装它不发生。
    """
    if not isinstance(expr, dict) or not expr:
        return None

    if "all" in expr:
        values = [evaluate_condition(e, state) for e in (expr.get("all") or [])]
        if any(v is False for v in values):
            return False
        return True if values and all(v is True for v in values) else None

    if "any" in expr:
        values = [evaluate_condition(e, state) for e in (expr.get("any") or [])]
        if any(v is True for v in values):
            return True
        return False if values and all(v is False for v in values) else None

    if "not" in expr:
        inner = evaluate_condition(expr.get("not"), state)
        return None if inner is None else not inner

    atoms = [k for k in expr if k in CONDITION_ATOMS]
    if len(atoms) != 1:
        return None
    atom = atoms[0]
    value = str(expr.get(atom) or "").strip()
    if not value:
        return None

    if atom == "flag":
        return bool(state.flags.get(value))
    if atom == "at":
        return bool(state.at) and state.at == value
    if atom == "visited":
        return value in state.visited
    if atom == "clue_known":
        return value in state.clues
    if atom == "has_item":
        return bool(state.items.get(value))
    if atom == "phase":
        return state.phase == value
    return None


def validate_condition(expr: Any, *, path: str = "condition") -> list[str]:
    """校验条件 AST 的形状；返回人类可读的错误列表（空 = 合法或 None）。"""
    if expr is None:
        return []
    if not isinstance(expr, dict) or not expr:
        return [f"{path}：条件必须是对象或 null"]
    if "all" in expr or "any" in expr:
        key = "all" if "all" in expr else "any"
        items = expr.get(key)
        if not isinstance(items, list) or not items:
            return [f"{path}.{key}：必须是至少一项的数组"]
        out: list[str] = []
        for i, item in enumerate(items):
            out.extend(validate_condition(item, path=f"{path}.{key}[{i}]"))
        return out
    if "not" in expr:
        return validate_condition(expr.get("not"), path=f"{path}.not")
    atoms = [k for k in expr if k in CONDITION_ATOMS]
    if len(atoms) != 1:
        return [f"{path}：未知条件原子（可用：{'、'.join(CONDITION_ATOMS)}）"]
    value = expr.get(atoms[0])
    if not isinstance(value, str) or not value.strip():
        return [f"{path}.{atoms[0]}：必须是非空字符串"]
    return []


# ── 效果校验 ──────────────────────────────────────────────────────────────


def validate_effect(effect: Any, *, path: str = "effect") -> list[str]:
    """校验单条效果（含 check 的嵌套分支）。"""
    if not isinstance(effect, dict):
        return [f"{path}：效果必须是对象"]
    etype = str(effect.get("type") or "").strip()
    if etype not in EFFECT_TYPES:
        return [f"{path}.type：未知效果 {etype!r}（可用：{'、'.join(EFFECT_TYPES)}）"]

    errors: list[str] = []
    for key in _EFFECT_REQUIRED.get(etype, ()):
        value = effect.get(key)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{path}.{key}：{etype} 必填且必须是非空字符串")

    if etype == "check":
        for branch in _BRANCH_KEYS:
            items = effect.get(branch)
            if items is None:
                continue
            if not isinstance(items, list):
                errors.append(f"{path}.{branch}：必须是效果数组")
                continue
            for i, child in enumerate(items):
                errors.extend(validate_effect(child, path=f"{path}.{branch}[{i}]"))
    else:
        for branch in _BRANCH_KEYS:
            if branch in effect:
                errors.append(f"{path}.{branch}：只有 check 可以带成功/失败分支")
    return errors


def iter_effects(effects: Iterable[Any]) -> Iterable[dict]:
    """深度遍历效果树（含 check 分支），产出所有效果对象。"""
    for effect in effects or []:
        if not isinstance(effect, dict):
            continue
        yield effect
        if str(effect.get("type") or "") == "check":
            for branch in _BRANCH_KEYS:
                yield from iter_effects(effect.get(branch) or [])


def effect_brief(effect: dict) -> str:
    """把单条效果压成一句给 KP 读的说明。"""
    etype = str(effect.get("type") or "")
    if etype == "check":
        skill = effect.get("skill") or "技能"
        diff = effect.get("difficulty") or ""
        head = f"{skill}检定" + (f"（{diff}）" if diff else "")
        branches = []
        for key, label in (("on_success", "成功"), ("on_failure", "失败")):
            children = effect.get(key) or []
            if children:
                branches.append(f"{label}：" + "，".join(effect_brief(c) for c in children))
        return head + (" → " + "；".join(branches) if branches else "")
    if etype == "sanity":
        return f"SAN 损失 {effect.get('loss')}（{effect.get('targets') or '当事人'}）"
    if etype == "damage":
        return f"HP 损失 {effect.get('dice')}"
    if etype == "heal":
        return f"HP 恢复 {effect.get('amount')}"
    if etype == "goto":
        who = effect.get("who") or "队伍"
        return f"{who}前往 {effect.get('target')}"
    if etype == "set_flag":
        return f"置标志 {effect.get('flag')}"
    if etype == "clear_flag":
        return f"清标志 {effect.get('flag')}"
    if etype == "reveal_clue":
        return f"揭示线索 {effect.get('clue_id')}"
    if etype == "grant_item":
        return f"获得 {effect.get('item')}"
    if etype == "remove_item":
        return f"失去 {effect.get('item')}"
    if etype == "npc_state":
        return f"NPC {effect.get('npc_id')} 状态变化"
    if etype == "unlock":
        return f"解锁 {effect.get('location_id')}"
    if etype == "block":
        return f"封路 {effect.get('location_id')}"
    if etype == "ending":
        return f"抵达结局 {effect.get('ending_id')}"
    if etype == "narration":
        return str(effect.get("text") or "")
    return etype


# ── 事件规整 ──────────────────────────────────────────────────────────────


def _str(value: Any) -> str:
    return str(value or "").strip()


def _normalize_when(raw: Any) -> dict:
    if isinstance(raw, dict):
        text = _str(raw.get("text"))
        expr = raw.get("expr")
        return {"text": text, "expr": expr if isinstance(expr, dict) else None}
    text = _str(raw)
    return {"text": text, "expr": None}


def _legacy_effects_from_scene_event(event: dict) -> list[dict]:
    """旧格式（kind=san_check / dice_check / damage / note）→ v3 效果。"""
    kind = _str(event.get("kind"))
    note = _str(event.get("note"))
    effects: list[dict] = []
    if kind == "san_check":
        effects.append({"type": "sanity", "loss": _str(event.get("san_loss")) or "0/0"})
    elif kind == "dice_check":
        effects.append({
            "type": "check",
            "skill": _str(event.get("skill")) or "技能",
            "on_success": [],
            "on_failure": [],
        })
    elif kind == "damage":
        effects.append({"type": "damage", "dice": _str(event.get("damage")) or "1d3"})
    if note:
        effects.append({"type": "narration", "text": note})
    if not effects:
        effects.append({"type": "narration", "text": _str(event.get("trigger"))})
    return effects


def normalize_event(raw: dict) -> dict | None:
    """把一个事件规整成运行时认识的样子；没有 id 的丢弃。"""
    if not isinstance(raw, dict):
        return None
    eid = _str(raw.get("id"))
    if not eid:
        return None
    effects = raw.get("effects")
    if not isinstance(effects, list):
        effects = []
    if not effects and raw.get("kind"):
        effects = _legacy_effects_from_scene_event(raw)
    return {
        "id": eid,
        "title": _str(raw.get("title") or raw.get("name")) or eid,
        "at": _str(raw.get("at") or raw.get("location")),
        "phase": _str(raw.get("phase")) or "scene",
        "once": bool(raw.get("once", True)),
        "when": _normalize_when(raw.get("when")),
        "npcs": [str(n).strip() for n in (raw.get("npcs") or []) if str(n).strip()],
        "effects": [e for e in effects if isinstance(e, dict)],
    }


def normalize_events(events: Any, scenes: Any = None) -> list[dict]:
    """规整事件数组，并把旧结构里 kind=event 的场景提升为事件。"""
    out: list[dict] = []
    seen: set[str] = set()
    for raw in events or []:
        ev = normalize_event(raw)
        if ev and ev["id"] not in seen:
            out.append(ev)
            seen.add(ev["id"])
    for scene in scenes or []:
        if not isinstance(scene, dict):
            continue
        if _str(scene.get("kind")).lower() != "event":
            continue
        sid = _str(scene.get("id"))
        if not sid or sid in seen:
            continue
        conns = [str(c).strip() for c in (scene.get("connections") or []) if str(c).strip()]
        effects: list[dict] = []
        for legacy in scene.get("events") or []:
            if isinstance(legacy, dict):
                effects.extend(_legacy_effects_from_scene_event(legacy))
        ev = normalize_event({
            "id": sid,
            "title": scene.get("title") or scene.get("name"),
            "at": conns[0] if conns else "",
            "phase": "opening" if "导入" in _str(scene.get("title")) else "scene",
            "once": True,
            "when": {"text": _str(scene.get("when")), "expr": None},
            "npcs": scene.get("npcs") or [],
            "effects": effects,
        })
        if ev:
            out.append(ev)
            seen.add(sid)
    return out


# ── 可触发事件 ────────────────────────────────────────────────────────────


def eligible_events(
    events: list[dict], state: FlowState,
) -> tuple[list[dict], list[dict]]:
    """按当前状态筛事件：返回 (可确定性触发, 需模型判断)。

    - when.expr 求值为 True → 可触发；
    - 求值为 False → 不出现；
    - expr 为空或求值为 None → 进 needs_judgement，由模型/规划器决定；
    - 已发生且 once 的事件不再出现。
    """
    eligible: list[dict] = []
    undecided: list[dict] = []
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        if ev.get("once", True) and ev.get("id") in state.seen:
            continue
        expr = (ev.get("when") or {}).get("expr")
        if not isinstance(expr, dict):
            undecided.append(ev)
            continue
        verdict = evaluate_condition(expr, state)
        if verdict is True:
            eligible.append(ev)
        elif verdict is None:
            undecided.append(ev)
    return eligible, undecided


# ── 整份模组校验 ──────────────────────────────────────────────────────────


def _issue(code: str, message: str, ref: str = "") -> dict:
    return {"code": code, "message": message, "ref": ref}


def _locations_of(data: dict) -> list[dict]:
    if isinstance(data.get("locations"), list):
        return [s for s in data["locations"] if isinstance(s, dict)]
    return [
        s for s in (data.get("scenes") or [])
        if isinstance(s, dict) and _str(s.get("kind")).lower() not in ("event", "chapter")
    ]


def _name_map(items: Iterable[dict]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items:
        if isinstance(item, dict) and _str(item.get("id")):
            out[_str(item["id"])] = _str(item.get("title") or item.get("name")) or _str(item["id"])
    return out


def _raw_exits(loc: dict) -> list:
    exits = loc.get("exits")
    if isinstance(exits, list):
        return exits
    conns = loc.get("connections")
    return conns if isinstance(conns, list) else []


def _exit_target(exit_: Any) -> str:
    if isinstance(exit_, dict):
        return _str(exit_.get("to"))
    return _str(exit_)


def validate_module(data: dict) -> dict:
    """校验整份模组，返回 errors/warnings/schema_version。

    只报**结构性问题**，不评判剧情好坏。errors 表示运行时一定出问题，
    warnings 表示能跑但大概率漏效果（如悬空 flag）。
    """
    data = data or {}
    errors: list[dict] = []
    warnings: list[dict] = []

    locations = _locations_of(data)
    events = normalize_events(data.get("events"), data.get("scenes"))
    endings = [e for e in (data.get("endings") or []) if isinstance(e, dict)]
    clues = [c for c in (data.get("clues") or []) if isinstance(c, dict)]
    npcs = [n for n in (data.get("npcs") or []) if isinstance(n, dict)]

    loc_ids = _name_map(locations)
    clue_ids = {_str(c.get("id")) for c in clues if _str(c.get("id"))}
    npc_ids = {_str(n.get("id")) for n in npcs if _str(n.get("id"))}
    ending_ids = {_str(e.get("id")) for e in endings if _str(e.get("id"))}

    # 1. id 唯一性
    for label, items in (("地点", locations), ("事件", events), ("结局", endings),
                         ("NPC", npcs), ("线索", clues)):
        seen: set[str] = set()
        for item in items:
            iid = _str(item.get("id"))
            if not iid:
                errors.append(_issue("MISSING_ID", f"{label}缺少 id"))
                continue
            if iid in seen:
                errors.append(_issue("DUPLICATE_ID", f"{label} id 重复：{iid}", iid))
            seen.add(iid)

    # 2. 事件：at 有效、条件/效果合法
    for ev in events:
        eid = ev["id"]
        at = _str(ev.get("at"))
        if at and at not in loc_ids:
            errors.append(_issue("EVENT_BAD_LOCATION", f"事件 {eid} 的 at 指向不存在的地点：{at}", eid))
        if not at:
            warnings.append(_issue("EVENT_NO_LOCATION", f"事件 {eid} 没有 at，无法定位发生地", eid))
        expr = (ev.get("when") or {}).get("expr")
        for msg in validate_condition(expr, path=f"事件 {eid}.when.expr"):
            errors.append(_issue("BAD_CONDITION", msg, eid))
        if not ev.get("effects"):
            warnings.append(_issue("EVENT_NO_EFFECT", f"事件 {eid} 没有任何效果，只能靠 KP 自由发挥", eid))
        for i, effect in enumerate(ev.get("effects") or []):
            for msg in validate_effect(effect, path=f"事件 {eid}.effects[{i}]"):
                errors.append(_issue("BAD_EFFECT", msg, eid))

    # 3. 效果引用完整性
    for ev in events:
        for effect in iter_effects(ev.get("effects") or []):
            etype = _str(effect.get("type"))
            for key, valid, label in (
                ("target", set(loc_ids), "地点"),
                ("clue_id", clue_ids, "线索"),
                ("ending_id", ending_ids, "结局"),
                ("npc_id", npc_ids, "NPC"),
                ("location_id", set(loc_ids), "地点"),
            ):
                if key not in effect:
                    continue
                ref = _str(effect.get(key))
                if ref and ref not in valid:
                    errors.append(_issue(
                        "BROKEN_REF",
                        f"事件 {ev['id']} 的 {etype}.{key} 指向不存在的{label}：{ref}",
                        ev["id"],
                    ))

    # 4. exits 引用
    for loc in locations:
        lid = _str(loc.get("id"))
        for exit_ in _raw_exits(loc):
            target = _exit_target(exit_)
            if target and target not in loc_ids:
                errors.append(_issue("BROKEN_EXIT", f"地点 {lid} 通往不存在的地点：{target}", lid))

    # 5. flag 生产 / 消费闭环
    produced: dict[str, list[str]] = {}
    consumed: dict[str, list[str]] = {}
    for ev in events:
        for effect in iter_effects(ev.get("effects") or []):
            etype = _str(effect.get("type"))
            flag = _str(effect.get("flag"))
            if not flag:
                continue
            if etype in ("set_flag", "clear_flag"):
                produced.setdefault(flag, []).append(ev["id"])
    for ev in events:
        expr = (ev.get("when") or {}).get("expr")
        for flag in _condition_flags(expr):
            consumed.setdefault(flag, []).append(ev["id"])
    for item in data.get("triggers") or []:
        if not isinstance(item, dict):
            continue
        for flag in item.get("set_flags") or []:
            produced.setdefault(_str(flag), []).append(_str(item.get("id")) or "trigger")
        for flag in item.get("clear_flags") or []:
            produced.setdefault(_str(flag), []).append(_str(item.get("id")) or "trigger")
    for loc in locations:
        for flag in _state_flags(loc.get("states")):
            consumed.setdefault(flag, []).append(_str(loc.get("id")))
    for npc in npcs:
        for flag in _state_flags(npc.get("states")):
            consumed.setdefault(flag, []).append(_str(npc.get("id")))
    for flag, producers in produced.items():
        if flag and flag not in consumed:
            warnings.append(_issue(
                "DANGLING_FLAG",
                f"标志 {flag} 被 {'、'.join(producers)} 置上，但没有任何条件或变体消费它",
                flag,
            ))

    # 6. 可达性
    start = _str((data.get("world") or {}).get("start")) or next(iter(loc_ids), "")
    edges: dict[str, set[str]] = {}
    for loc in locations:
        lid = _str(loc.get("id"))
        targets: set[str] = set()
        for exit_ in _raw_exits(loc):
            target = _exit_target(exit_)
            if target in loc_ids:
                targets.add(target)
        edges[lid] = targets
    if start and start in edges:
        reached = {start}
        queue = [start]
        while queue:
            cur = queue.pop()
            for nxt in edges.get(cur, ()):
                if nxt not in reached:
                    reached.add(nxt)
                    queue.append(nxt)
        for lid, name in loc_ids.items():
            if lid not in reached:
                warnings.append(_issue("UNREACHABLE_LOCATION", f"地点 {name}（{lid}）从起点不可达", lid))

    # 7. 结局
    for ending in endings:
        eid = _str(ending.get("id"))
        when = ending.get("when")
        text = _str(when.get("text") if isinstance(when, dict) else when)
        expr = when.get("expr") if isinstance(when, dict) else None
        if not text and not expr:
            warnings.append(_issue("ENDING_NO_CONDITION", f"结局 {eid} 没有达成条件", eid))
        for msg in validate_condition(expr, path=f"结局 {eid}.when.expr"):
            errors.append(_issue("BAD_CONDITION", msg, eid))

    return {
        "schema_version": SCHEMA_VERSION,
        "errors": errors,
        "warnings": warnings,
    }


def _condition_flags(expr: Any) -> set[str]:
    """递归收集条件里出现的 flag 名。"""
    out: set[str] = set()
    if not isinstance(expr, dict):
        return out
    if "all" in expr or "any" in expr:
        for item in expr.get("all") or expr.get("any") or []:
            out |= _condition_flags(item)
        return out
    if "not" in expr:
        return _condition_flags(expr.get("not"))
    if _str(expr.get("flag")):
        out.add(_str(expr.get("flag")))
    return out


def _state_flags(states: Any) -> set[str]:
    out: set[str] = set()
    for state in states or []:
        if isinstance(state, dict):
            for flag in state.get("when") or []:
                if _str(flag):
                    out.add(_str(flag))
    return out


def _is_event_scene(scene: dict) -> bool:
    return _str((scene or {}).get("kind")).lower() == "event"


def upgrade_legacy_module(data: dict) -> dict:
    """把旧结构模组规整成 v3（纯函数，不改入参）。

    - scenes 里的 kind=event 提升到 events，并从地点列表移除；
    - world.start 缺失时取第一个地点；
    - endings.when 字符串包成 {text, expr: null}；
    - 重算 validation，写入 schema_version=3。

    返回新 dict，调用方可直接落库，也可只用来做 dry-run 对比。
    """
    data = dict(data or {})
    scenes = [dict(s) for s in (data.get("scenes") or []) if isinstance(s, dict)]
    events = normalize_events(data.get("events"), scenes)
    locations = [s for s in scenes if not _is_event_scene(s)]

    world = dict(data.get("world") or data.get("world_setting") or {})
    if not _str(world.get("start")):
        first = next((s for s in locations if _str(s.get("id"))), None)
        if first:
            world["start"] = _str(first.get("id"))

    endings = []
    for ending in data.get("endings") or []:
        if not isinstance(ending, dict):
            continue
        item = dict(ending)
        when = item.get("when")
        if isinstance(when, str):
            item["when"] = {"text": when.strip(), "expr": None}
        endings.append(item)

    out = {
        **data,
        "world": world,
        "scenes": locations,
        "events": events,
        "endings": endings,
    }
    out["schema_version"] = SCHEMA_VERSION
    out["validation"] = validate_module(out)
    return out
