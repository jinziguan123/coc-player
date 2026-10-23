"""剧情进度外置：规划器裁定的剧情标志经校验后确定性落库。

过去剧情标志（world_state.flags）**只**靠 KP 记得调 ``set_flag``——实测长局里几乎从不调用，
于是系统不知道剧情推进到哪：事件闸门永远停在「未触发」、幕后推演只能凭摘要自由发挥，
一旦滚动摘要压掉了那段叙事，进度就彻底丢了。

现在与场景/库存/SAN 守卫同一思路：规划器每轮对照「尚未触发的剧情钩子」（模组 triggers +
kind=event 的剧情事件）判断本轮是否满足了某个条件，填 ``scene_policy.set_flags`` 并给出理由；
叙事结束后由这里校验并落库：

- **只认模组里写明的标志**：triggers 的 set_flags/clear_flags 与剧情事件的 set_flags，
  模型编出来的标志名一律丢弃（规划器不能凭空发明剧情状态）；
- 已激活的不重复置、未激活的不清；
- 每次落实记一条**仅 KP 可见**的系统事件（钩子 + 理由），便于事后回溯与观察；
- 落标志复用 ``turn_effects._exec_flag``——场景状态配图等联动与 KP 亲手置标志一致。

KP 上下文、规划器与幕后推演都直接读 world_state.flags，故落标志后本轮收尾的幕后推演、
下一轮的 KP 叙事立即以新进度为准。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.orm import Session

from app.models.session import GameSession
from app.services import effect_channel, entity_state, scene_kind, session_service

logger = logging.getLogger(__name__)

PLOT_PROGRESS_KIND = "plot_progress"
_REASON_MAX = 120


def _str_list(v: Any) -> list[str]:
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, (list, tuple)):
        return []
    out: list[str] = []
    for x in v:
        s = str(x or "").strip()
        if s and s not in out:
            out.append(s)
    return out


def _trigger_sets(trig: dict) -> list[str]:
    return _str_list(trig.get("set_flags") or trig.get("flags"))


def _hooks(module: Any) -> list[dict]:
    """模组里全部剧情钩子（triggers + 剧情事件），统一成 {id, title, when, set_flags, clear_flags, kind}。"""
    out: list[dict] = []
    for i, trig in enumerate(getattr(module, "triggers", None) or []):
        if not isinstance(trig, dict):
            continue
        sets, clears = _trigger_sets(trig), _str_list(trig.get("clear_flags"))
        if not (sets or clears):
            continue
        when = str(trig.get("when") or trig.get("condition") or "").strip()
        out.append({
            "id": str(trig.get("id") or f"trigger_{i + 1}"),
            "title": str(trig.get("description") or when).strip()[:80],
            "when": when,
            "set_flags": sets,
            "clear_flags": clears,
            "kind": "trigger",
        })
    for e in scene_kind.event_scenes(getattr(module, "scenes", None)):
        out.append({
            "id": str(e.get("id") or ""),
            "title": scene_kind.event_title(e),
            "when": str(e.get("when") or "").strip(),
            "set_flags": scene_kind.event_flags(e),
            "clear_flags": [],
            "kind": "event",
        })
    return out


def _hook_done(hook: dict, flags: set[str]) -> bool:
    """钩子是否已触发：trigger 要求 set_flags 全部激活（与导演信号同口径）；事件任一激活即算
    （与 scene_kind.event_triggered 同口径）。只有 clear_flags 的 trigger 以「都已清掉」为准。"""
    sets = hook["set_flags"]
    if not sets:
        return not any(f in flags for f in hook["clear_flags"])
    if hook["kind"] == "event":
        return any(f in flags for f in sets)
    return all(f in flags for f in sets)


def pending_hooks(module: Any, flags: set[str]) -> list[dict]:
    """尚未触发的剧情钩子——喂给规划器判断本轮是否满足条件。"""
    return [
        {k: h[k] for k in ("id", "title", "when", "set_flags", "clear_flags")}
        for h in _hooks(module) if not _hook_done(h, flags)
    ]


def legal_flags(module: Any) -> tuple[set[str], set[str]]:
    """(可置的标志, 可清的标志)：只认模组 triggers / 剧情事件里写明的标志名。"""
    can_set: set[str] = set()
    can_clear: set[str] = set()
    for h in _hooks(module):
        can_set.update(h["set_flags"])
        can_clear.update(h["clear_flags"])
    return can_set, can_clear


def _state_flags(module: Any) -> set[str]:
    """模组实体（场景/NPC/线索/物品/手书）``states[].when`` 里引用的标志：KP 置它们会切换实体状态。"""
    out: set[str] = set()
    for field in ("scenes", "npcs", "clues", "items", "handouts"):
        for entity in getattr(module, field, None) or []:
            if not isinstance(entity, dict):
                continue
            for st in entity.get("states") or []:
                if isinstance(st, dict):
                    out.update(_str_list(st.get("when")))
    return out


def model_flag_rejection(module: Any, flags: set[str], flag: str, value: bool) -> str:
    """模型来源（KP 工具/文本指令）的置/清标志合法性：合法返回空串，否则返回给 KP 的说明。

    与规划器同一白名单口径（模组钩子写明的标志），另认实体状态变体引用的标志；
    真人 KP 的明确操作不经此校验。清除只回收已激活且模组认得的标志。
    """
    can_set, can_clear = legal_flags(module)
    known = can_set | can_clear | _state_flags(module)
    flag = str(flag or "").strip()
    if not flag:
        return "参数缺失：flag 为必填。"
    if value:
        if flag not in known:
            return f"标志 {flag} 不在模组剧情钩子或实体状态里，已忽略；剧情推进请用叙事交代。"
        return ""
    if flag not in flags:
        return f"标志 {flag} 当前未激活，无需清除。"
    if flag not in known:
        return f"标志 {flag} 不在模组剧情钩子或实体状态里，已忽略。"
    return ""


def validate_plan_flags(
    module: Any, flags: set[str], set_flags: list[str] | None, clear_flags: list[str] | None,
) -> tuple[list[str], list[str]]:
    """把规划器给的标志过一遍白名单：返回 (真要置的, 真要清的)。

    非法标志名（模组里没有）丢弃并告警；已激活的不重复置；未激活的不清；
    同一标志同时被要求置与清时以「置」为准（推进优先，清除只是回收）。
    """
    can_set, can_clear = legal_flags(module)
    to_set: list[str] = []
    for f in _str_list(set_flags):
        if f not in can_set:
            logger.warning("规划器给出的剧情标志不在模组钩子里，已丢弃：%s", f)
        elif f not in flags and f not in to_set:
            to_set.append(f)
    to_clear: list[str] = []
    for f in _str_list(clear_flags):
        if f not in can_clear:
            logger.warning("规划器要清的剧情标志不在模组钩子里，已丢弃：%s", f)
        elif f in flags and f not in to_set and f not in to_clear:
            to_clear.append(f)
    return to_set, to_clear


def sanitize_plan_flags(plan: Any, module: Any, flags: set[str]) -> None:
    """规划器出结果后立即过白名单（原地改 plan）：KP 收到的裁定计划里只剩合法的标志。

    叙事前就清洗，KP 才不会照着一个编出来的标志去叙述「剧情推进」；
    叙事后 ``apply_planned_flags`` 落库时还会按最新 flags 再校验一次。
    """
    policy = getattr(plan, "scene_policy", None)
    if policy is None or not (policy.set_flags or policy.clear_flags):
        return
    to_set, to_clear = validate_plan_flags(module, flags, policy.set_flags, policy.clear_flags)
    policy.set_flags, policy.clear_flags = to_set, to_clear
    if not (to_set or to_clear):
        policy.flag_reason = ""


def _progress_note(module: Any, flags_before: set[str], to_set: list[str],
                   to_clear: list[str], reason: str) -> str:
    """KP 专属事件正文：哪个钩子被触发 + 规划器理由 + 落了哪些标志。"""
    touched = set(to_set) | set(to_clear)
    titles = [
        h["title"] or h["id"] for h in _hooks(module)
        if not _hook_done(h, flags_before)
        and touched & (set(h["set_flags"]) | set(h["clear_flags"]))
    ]
    parts = ["剧情推进"]
    if titles:
        parts.append("：" + "；".join(f"「{t}」" for t in titles) + " 条件已满足")
    ops = []
    if to_set:
        ops.append("置 " + "、".join(to_set))
    if to_clear:
        ops.append("清 " + "、".join(to_clear))
    text = "".join(parts) + "（" + "，".join(ops) + "）"
    reason = (reason or "").strip()
    if reason:
        text += "。依据：" + reason[:_REASON_MAX]
    return text


async def apply_planned_flags(
    db: Session, session_id: str, game_session: GameSession, module: Any, plan: Any,
) -> AsyncIterator[str]:
    """叙事后落实规划器裁定的剧情标志（校验 + 落库 + KP 专属事件）。

    放在全部守卫之后、收尾（摘要/幕后推演）之前：本轮收尾的幕后推演就能看到新进度。
    fail-open：任何异常只记日志，绝不阻断跑团。
    """
    if plan is None or module is None:
        return
    policy = getattr(plan, "scene_policy", None)
    if policy is None or not (policy.set_flags or policy.clear_flags):
        return
    # 延迟导入：turn_effects → turn_context → turn_planner → 本模块，顶层导入会成环
    from app.services import turn_effects
    try:
        db.refresh(game_session)
        before = entity_state.active_flags(game_session)
        to_set, to_clear = validate_plan_flags(
            module, before, policy.set_flags, policy.clear_flags,
        )
        if not (to_set or to_clear):
            return
        note = _progress_note(
            module, before, to_set, to_clear, getattr(policy, "flag_reason", "") or "",
        )

        def apply(receipt: dict) -> None:
            for f in to_set:
                session_service.set_flag(db, session_id, f, True, commit=False)
            for f in to_clear:
                session_service.set_flag(db, session_id, f, False, commit=False)
            # 只落库不广播：标志名是剧情骨架，玩家永远不可见（同幕后事件）
            session_service.add_event(
                db, session_id, "system", note, actor_name="剧情",
                visibility=[session_service.KP_ONLY_SENTINEL], commit=False,
                metadata={
                    "kind": PLOT_PROGRESS_KIND, "set_flags": to_set, "clear_flags": to_clear,
                    "effect": receipt,
                },
            )

        # 全部标志与记录同一事务（效果通道）：不会出现「标志落了一半、没有推进记录」。
        effect_channel.execute(
            db, session_id,
            effect_channel.EffectProposal(
                effect_channel.FlagChange(tuple(to_set), tuple(to_clear)), "planner", 0,
            ),
            apply, duplicate=None, already=lambda: False,
        )
        # 配图等联动只在提交之后做一次。
        chunks = turn_effects._after_flags_changed(db, session_id, game_session)
        logger.info(
            "规划器剧情标志落实：session=%s 置=%s 清=%s", session_id, to_set, to_clear,
        )
    except Exception:
        logger.exception("规划器剧情标志落实失败（忽略）: session=%s", session_id)
        return
    for chunk in chunks:
        yield chunk
