"""按异步任务累加「本次生成」里所有 LLM 调用的服务端 usage，用于「本局累计 token 消耗」。

用 contextvar 承载累加器：天然按 asyncio task 隔离（并发多局互不干扰），且自动传播到
task 内 await 的所有子调用——一个回合里 planner、主叙事、validator、AI 队友、NPC/幕后
子代理、战斗叙述等即便用不同的 Provider 实例，也都记进同一个累加器。

Provider 每拿到服务端 usage 就 ``add()``；生成入口协程由 ``generation_manager`` 用
``tracked()`` 包一层（生成之外属于本局的 API 入口用 ``scope()``），结束（含取消/异常）时
把本次合计累进 ``session_stats.session_usage``，按环节的账本累进 ``usage_by_agent``。
无累加器（如脱离本局的零散调用）时 ``add()`` 只记进当前轨迹段（若有）；全程 fail-open。
"""

from __future__ import annotations

import contextlib
import contextvars
import functools
import inspect
import logging

from app.services import session_stats

logger = logging.getLogger(__name__)

#: reasoning_tokens 单独记：思考型模型把它算进 completion_tokens，但内容会被丢弃
#: （complete() 只收 delta.content）。不拆开看，就分不清「模型话多」和「模型在空想」——
#: 前者要改提示词，后者只需把思考等级调低，解法完全不同。
#: cache_read / cache_write 同理单独记：命中的部分只按约 0.1× 计费、写入按 1.25~2×，
#: 不拆开就看不出 prompt caching 到底有没有生效。命中率长期为 0 = 提示词前缀里混进了
#: 每轮都变的内容（见 app.ai.context 的分段装配）。无缓存能力的 Provider 恒为 0。
_FIELDS = (
    "prompt_tokens", "completion_tokens", "reasoning_tokens", "total_tokens",
    "cache_read_tokens", "cache_write_tokens", "calls",
)
_acc: contextvars.ContextVar[dict | None] = contextvars.ContextVar("llm_usage_acc", default=None)


def _zero() -> dict:
    return {k: 0 for k in _FIELDS}


#: 轨迹段（agent_trace 的一段）自己的累加器。**独占**口径：调用只记进最内层那一段，
#: 嵌套的内层段（如 KP 工具循环里调起的 NPC 代演）不再算进外层；并发的子任务（各队友
#: 决策各自一个 task）复制各自的上下文，互不串账。于是各段之和不会超过本局总量，
#: 差额就是「没挂在任何一段上」的调用——面板据此对账。
_span: contextvars.ContextVar[dict | None] = contextvars.ContextVar("llm_usage_span", default=None)
#: 按环节（agent 名）的合计账本，随总累加器一起落账到 session_stats.usage_by_agent。
#: 段结束时把该段用量并进来；持有的是同一个 dict，子任务里结束的段也记得上。
_by_agent: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "llm_usage_by_agent", default=None,
)
#: 当前记账所属的局：底层模块（终检、去口癖、摘要…）开轨迹段时不必层层传 session_id。
_session: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "llm_usage_session", default=None,
)
#: 顶层生成路径的名字（「开场」「投骰」「重新生成」…），拼进轨迹的 stage 里，
#: 面板上一眼看出「这次主叙事是哪条路径上的」。
_phase: contextvars.ContextVar[str] = contextvars.ContextVar("llm_usage_phase", default="")


def _normalize(usage: dict) -> dict:
    """把一次调用的服务端 usage 归一到 _FIELDS 口径（calls 记 1）。"""
    d = _zero()
    d["prompt_tokens"] = int(usage.get("prompt_tokens") or 0)
    d["completion_tokens"] = int(usage.get("completion_tokens") or 0)
    d["total_tokens"] = int(usage.get("total_tokens") or 0)
    # 思考 token 挂在 completion_tokens_details 下（OpenAI 与 DeepSeek 同构）；没有就算 0。
    details = usage.get("completion_tokens_details")
    if isinstance(details, dict):
        d["reasoning_tokens"] = int(details.get("reasoning_tokens") or 0)
    # prompt caching 用量：Anthropic 直接下发这两个字段；OpenAI 兼容端（如 DeepSeek 的
    # 自动上下文缓存）用 prompt_cache_hit/miss_tokens，命中口径相同，一并归一。
    d["cache_read_tokens"] = int(
        usage.get("cache_read_input_tokens") or usage.get("prompt_cache_hit_tokens") or 0
    )
    d["cache_write_tokens"] = int(usage.get("cache_creation_input_tokens") or 0)
    d["calls"] = 1
    return d


def _add_into(acc: dict, d: dict) -> None:
    for k in _FIELDS:
        acc[k] = int(acc.get(k) or 0) + int(d.get(k) or 0)


def add(usage: dict | None) -> None:
    """把一次调用的服务端 usage 累加进当前任务的累加器与当前轨迹段（都没有/无效 usage 时忽略）。

    轨迹段不依赖总累加器：脱离生成的零散调用也能在自己那一段里记上用量。
    """
    if not isinstance(usage, dict):
        return
    acc, span = _acc.get(), _span.get()
    if acc is None and span is None:
        return
    d = _normalize(usage)
    if acc is not None:
        _add_into(acc, d)
    if span is not None:
        _add_into(span, d)


def open_span() -> tuple[dict, contextvars.Token]:
    """开一段独占记账：返回该段的累加器与复位用的 token（交给 close_span）。"""
    acc = _zero()
    return acc, _span.set(acc)


def close_span(acc: dict, token: contextvars.Token, agent: str) -> None:
    """收段：恢复外层段，并把本段用量并进按环节的账本。

    在别的上下文里收（异步生成器被 GC 在别处 aclose 时会这样）reset 会抛 ValueError——
    这时外层上下文早已不在，照样把用量记账，只跳过复位。
    """
    try:
        _span.reset(token)
    except ValueError:
        pass
    ledger = _by_agent.get()
    if ledger is not None and acc.get("calls"):
        ledger[agent] = accumulate(ledger.get(agent), acc)


def current_session() -> str | None:
    return _session.get()


def current_phase() -> str:
    return _phase.get()


def enter_phase(label: str) -> None:
    """不复位地标记当前路径——只用在生成任务协程的开头（任务有自己的上下文副本，
    结束即作废）。能包一层的地方用 ``phase()``。"""
    _phase.set(label)


@contextlib.contextmanager
def phase(label: str):
    """标记当前所在的顶层生成路径（见 _phase）。"""
    token = _phase.set(label)
    try:
        yield
    finally:
        try:
            _phase.reset(token)
        except ValueError:
            pass


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
    # 命中率按「命中 / 总输入」算：入量本身已含命中部分（见 provider 的 _set_usage 归一）。
    cr = int(d.get("cache_read_tokens") or 0)
    cache = f"，缓存命中 {cr / max(pt, 1) * 100:.0f}%" if cr else ""
    return f"{calls} 次调用，入 {pt / 1000:.1f}k / 出 {ct / 1000:.1f}k{tail}{cache}"


#: 触发提醒要**同时**满足比例与绝对量两个条件。
#:
#: 只看比例会误报：关掉快模型的思考之后，一个 34.6s 的回合输出降到 2.2k、其中主模型思考
#: 1.6k——比例 70% 但总共才等半分钟，没什么可提醒的。只看绝对量则会漏掉「输出巨大且几乎
#: 全是思考」的情形。两个都卡住才是真的「在空想」。
#: 3k 的门槛按实测吞吐（约 110 tok/s）折算约等于 27 秒纯思考，值得说一句。
_REASONING_WARN_RATIO = 0.6
_REASONING_WARN_TOKENS = 3000


def warn_if_reasoning_dominates(snap: dict) -> None:
    """思考 token 又多又占大头时提醒一句，并指向真正能关掉它的开关。

    这条提醒是为了让「跑一个回合要等两分钟」能自己解释自己：思考内容会被 complete() 丢弃
    （只收 delta.content），于是时间照花、产物照扔。

    **不要在这里推荐调思考等级**：那只调强度、关不掉思考。实测 deepseek-v4-flash 下发
    reasoning_effort=low/minimal 后思考反而涨到留空时的 5 倍以上。真正的开关是配置里的
    「关闭模型思考」（下发 thinking={"type":"disabled"}，实测思考恒为 0）。
    """
    ct = int(snap.get("completion_tokens") or 0)
    rt = int(snap.get("reasoning_tokens") or 0)
    if ct <= 0 or rt < _REASONING_WARN_TOKENS or rt / ct < _REASONING_WARN_RATIO:
        return
    logger.warning(
        "本回合 %.0f%% 的输出是模型思考（%.1fk/%.1fk），落到正文的只有 %.1fk——"
        "这些思考内容生成完即被丢弃，时间却照花。想提速就在「设置 → AI 配置 → 编辑配置 → "
        "能力」里勾上「关闭模型思考」；先给**快模型**勾"
        "（planner / 终检 / 去口癖 / 滚动摘要走的都是它），"
        "主叙事那档可留着思考换文笔。注意「思考等级」只调强度、关不掉思考。",
        rt / ct * 100, rt / 1000, ct / 1000, (ct - rt) / 1000,
    )


def accumulate(cur: dict | None, snap: dict) -> dict:
    """把一次生成的 usage 合计累进累计用量（纯函数，返回新的 session_usage dict，单调累增）。"""
    cur = dict(cur or _zero())
    for k in _FIELDS:
        cur[k] = int(cur.get(k) or 0) + int(snap.get(k) or 0)
    return cur


async def tracked(session_id: str, coro) -> None:
    """包住一个生成协程：起累加器 → 执行 → 把本次合计累进该局 session_usage（fail-open）。

    取消/异常时 finally 仍会把已产生的用量记账（半截生成也花了 token），随后原样上抛。
    """
    # 生成任务是自己那一笔账的根：即便是从某个 API 记账范围里起的 task（复制了外层上下文），
    # 也要另起一本——外层请求早就返回、落过账了，透传过去这次生成的用量就丢了。
    async with scope(session_id, fresh=True):
        await coro


@contextlib.asynccontextmanager
async def scope(session_id: str, *, fresh: bool = False):
    """本局一段记账：起累加器 → 执行 → 把合计与按环节的账本累进 session_stats（fail-open）。

    生成任务之外、但属于本局的调用（真人 KP 参谋、KP 指令 NPC 代演、战斗路由驱动 NPC…）
    在 API 入口包这一层。已在某个记账范围里（如这些逻辑被生成任务调到）就原样透传，
    由外层统一落账，避免同一笔记两次。
    """
    if _acc.get() is not None and not fresh:
        yield
        return
    tokens = (
        (_acc, _acc.set(_zero())),
        (_by_agent, _by_agent.set({})),
        (_session, _session.set(session_id)),
        (_span, _span.set(None)),   # 新的一本账不继承外层的轨迹段
    )
    try:
        yield
    finally:
        snap = snapshot()
        by_agent = _by_agent.get() or {}
        for var, token in reversed(tokens):
            try:
                var.reset(token)
            except ValueError:
                pass
        if snap.get("calls"):
            _persist(session_id, snap, by_agent)


def session_scoped(endpoint):
    """API 端点装饰器：整个请求在本局（取路径参数 session_id）的记账范围里跑。

    给生成任务之外、但属于本局的 AI 调用入口用（真人 KP 参谋、KP 指令 NPC 代演、
    战斗路由驱动 NPC…）。functools.wraps 保留签名，FastAPI 依赖注入照常解析。
    放在路由/限流装饰器的最内层。
    """
    signature = inspect.signature(endpoint)

    @functools.wraps(endpoint)
    async def wrapper(*args, **kwargs):
        session_id = signature.bind_partial(*args, **kwargs).arguments["session_id"]
        async with scope(str(session_id)):
            return await endpoint(*args, **kwargs)

    return wrapper


def accumulate_by_agent(cur: dict | None, base_usage: dict | None, by_agent: dict) -> dict:
    """把一次记账的按环节账本累进 session_stats.usage_by_agent（纯函数，返回新 dict）。

    结构：``{"since": <账本启用时的 session_usage>, "agents": {agent: 用量}}``。since 是
    基线：账本晚于累计上线，老存档的累计里有一段从没分过环节的历史，对账时要先扣掉。
    """
    cur = dict(cur or {})
    if "since" not in cur:
        cur["since"] = accumulate(None, base_usage or {})
    agents = dict(cur.get("agents") or {})
    for agent, usage in by_agent.items():
        agents[agent] = accumulate(agents.get(agent), usage)
    cur["agents"] = agents
    return cur


def _persist(session_id: str, snap: dict, by_agent: dict | None = None) -> None:
    # SessionLocal/GameSession 保持局部导入：这里是「开一个新 DB 会话」的运行时行为，
    # 顶层引 app.database 会让 usage_tracker 在导入期就绑定引擎，影响测试替换。
    from app.database import SessionLocal
    from app.models.session import GameSession

    db = SessionLocal()
    try:
        if db.get(GameSession, session_id) is None:
            return
        stats = session_stats.get_or_create(db, session_id)
        stats.usage_by_agent = accumulate_by_agent(
            stats.usage_by_agent, stats.session_usage, by_agent or {},
        )
        stats.session_usage = accumulate(stats.session_usage, snap)
        db.commit()
    except Exception:
        logger.exception("累计本局 token 用量失败（忽略）: session=%s", session_id)
        db.rollback()
    finally:
        db.close()
