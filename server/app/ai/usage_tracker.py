"""按异步任务累加「本次生成」里所有 LLM 调用的服务端 usage，用于「本局累计 token 消耗」。

用 contextvar 承载累加器：天然按 asyncio task 隔离（并发多局互不干扰），且自动传播到
task 内 await 的所有子调用——一个回合里 planner、主叙事、validator、AI 队友、NPC/幕后
子代理、战斗叙述等即便用不同的 Provider 实例，也都记进同一个累加器。

Provider 每拿到服务端 usage 就 ``add()``；生成入口协程由 ``generation_manager`` 用
``tracked()`` 包一层，结束（含取消/异常）时把本次合计累进 ``world_state.session_usage``。
无累加器（如脱离生成的零散调用）时 ``add()`` 静默忽略；全程 fail-open。
"""

from __future__ import annotations

import contextvars
import logging

logger = logging.getLogger(__name__)

#: reasoning_tokens 单独记：思考型模型把它算进 completion_tokens，但内容会被丢弃
#: （complete() 只收 delta.content）。不拆开看，就分不清「模型话多」和「模型在空想」——
#: 前者要改提示词，后者只需把思考等级调低，解法完全不同。
_FIELDS = ("prompt_tokens", "completion_tokens", "reasoning_tokens", "total_tokens", "calls")
_acc: contextvars.ContextVar[dict | None] = contextvars.ContextVar("llm_usage_acc", default=None)


def _zero() -> dict:
    return {k: 0 for k in _FIELDS}


def add(usage: dict | None) -> None:
    """把一次调用的服务端 usage 累加进当前任务的累加器（无累加器/无效 usage 时忽略）。"""
    if not isinstance(usage, dict):
        return
    acc = _acc.get()
    if acc is None:
        return
    acc["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
    acc["completion_tokens"] += int(usage.get("completion_tokens") or 0)
    acc["total_tokens"] += int(usage.get("total_tokens") or 0)
    # 思考 token 挂在 completion_tokens_details 下（OpenAI 与 DeepSeek 同构）；没有就算 0。
    details = usage.get("completion_tokens_details")
    if isinstance(details, dict):
        acc["reasoning_tokens"] += int(details.get("reasoning_tokens") or 0)
    acc["calls"] += 1


def snapshot() -> dict:
    """取当前任务累加器的合计（无则全 0）。"""
    acc = _acc.get()
    return dict(acc) if acc else _zero()


def delta(before: dict, after: dict | None = None) -> dict:
    """两次 snapshot 之差＝这中间所有 LLM 调用的合计。用于把用量绑到某个阶段上。

    比读 provider 的 last_usage 准：一个阶段里往往不止一次调用（工具轮次、校验重写、
    并行的队友决策），last_usage 只剩最后一次，差值才是这个阶段真正花掉的。
    """
    after = snapshot() if after is None else after
    return {k: int(after.get(k) or 0) - int(before.get(k) or 0) for k in _FIELDS}


def fmt(d: dict) -> str:
    """把用量格式化成跟在耗时后面的短串：`3 次调用，入 45.2k / 出 18.3k（思考 12.1k）`。

    只看时间没法判断慢在哪，三种情况的解法完全不同：
      · 入大 → 上下文该裁了；
      · 出大且思考占大头 → 把思考等级调低（那些 token 生成完就被丢弃，纯属白等）；
      · 出大但思考很少 → 是提示词让模型话多，要改提示词或缩小输出结构。
    """
    calls = int(d.get("calls") or 0)
    pt, ct = int(d.get("prompt_tokens") or 0), int(d.get("completion_tokens") or 0)
    rt = int(d.get("reasoning_tokens") or 0)
    tail = f"（思考 {rt / 1000:.1f}k）" if rt else ""
    return f"{calls} 次调用，入 {pt / 1000:.1f}k / 出 {ct / 1000:.1f}k{tail}"


#: 思考占输出的比例超过这个数就提醒。取 0.6：偶尔想得多是正常的，长期六成以上说明
#: 模型基本在空想——实测一个 132.9s 的回合里思考占了 86%，落到文本的只有 1.2k token。
_REASONING_WARN_RATIO = 0.6


def warn_if_reasoning_dominates(snap: dict) -> None:
    """思考 token 占了输出的大头就提醒一句，并说清怎么改。

    这条提醒是为了让「跑一个回合要等两分钟」能自己解释自己。思考型模型默认多是开着的，
    而设置页把思考等级**留空的语义是「不下发该参数、用模型默认档」**，不是关闭——
    很容易误以为已经关了。思考内容还会被 complete() 丢弃（只收 delta.content），
    于是时间照花、产物照扔。
    """
    ct = int(snap.get("completion_tokens") or 0)
    rt = int(snap.get("reasoning_tokens") or 0)
    if ct <= 0 or rt / ct < _REASONING_WARN_RATIO:
        return
    logger.warning(
        "本回合 %.0f%% 的输出是模型思考（%.1fk/%.1fk），落到正文的只有 %.1fk。"
        "思考型模型默认多为开启，设置页「思考等级」留空只是不下发该参数、仍用模型默认档；"
        "嫌慢请显式填 minimal 或 low，而不是清空。",
        rt / ct * 100, rt / 1000, ct / 1000, (ct - rt) / 1000,
    )


def accumulate(ws: dict | None, snap: dict) -> dict:
    """把一次生成的 usage 合计累进 world_state.session_usage（纯函数，返回新 ws，单调累增）。"""
    cur = dict((ws or {}).get("session_usage") or _zero())
    for k in _FIELDS:
        cur[k] = int(cur.get(k) or 0) + int(snap.get(k) or 0)
    new_ws = dict(ws or {})
    new_ws["session_usage"] = cur
    return new_ws


async def tracked(session_id: str, coro) -> None:
    """包住一个生成协程：起累加器 → 执行 → 把本次合计累进该局 session_usage（fail-open）。

    取消/异常时 finally 仍会把已产生的用量记账（半截生成也花了 token），随后原样上抛。
    """
    _acc.set(_zero())
    try:
        await coro
    finally:
        snap = snapshot()
        if snap.get("calls"):
            _persist(session_id, snap)


def _persist(session_id: str, snap: dict) -> None:
    from app.database import SessionLocal
    from app.models.session import GameSession

    db = SessionLocal()
    try:
        gs = db.get(GameSession, session_id)
        if gs is not None:
            gs.world_state = accumulate(dict(gs.world_state or {}), snap)
            db.commit()
    except Exception:
        logger.exception("累计本局 token 用量失败（忽略）: session=%s", session_id)
        db.rollback()
    finally:
        db.close()
