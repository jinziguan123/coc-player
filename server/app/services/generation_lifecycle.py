"""生成错误分类与后台收尾任务生命周期。"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import weakref
from collections.abc import Awaitable, Callable

import httpx

from app.ai import usage_tracker, call_budget
from app.config import settings

logger = logging.getLogger(__name__)

HousekeepingFn = Callable[[object, str, object], Awaitable[None]]

#: 「等上轮收尾」低于这个秒数就不记：后台没活干时它恒为 0，每轮记一条纯噪音。
_DRAIN_LOG_THRESHOLD = 0.5


@contextlib.asynccontextmanager
async def generation_timing(
    session_id: str,
    label: str,
    *,
    drain: Callable[[str], Awaitable[None]] | None = None,
):
    """给一条顶层生成路径记「等上轮收尾 / 合计 / 思考占比」。

    每个交给 ``generation_manager.start`` 的协程都该包一层。开场是反面教材：它是玩家
    第一次等待，却一处埋点都没有——实测一次开场 3 分 42 秒，98% 的输出 token 是模型思考
    （生成完即被丢弃、不进旁白），而日志里连「花了多久」都查不到，只能靠体感描述。

    ``drain`` 给定时在计时开始后立刻排干上一轮的后台收尾——那段等待也算玩家的等待，
    必须计进合计里。不传则不排干（如真人 KP 的队友回合，本就不碰 world_state）。

    合计写在 ``finally``：生成失败或被取消时**更**该知道时间花在哪儿。
    """
    with usage_tracker.phase(label):
        async with _timing_body(session_id, label, drain):
            yield


@contextlib.asynccontextmanager
async def _timing_body(session_id: str, label: str, drain):
    started = time.monotonic()
    if drain is not None:
        t_drain = time.monotonic()
        await drain(session_id)
        drain_s = time.monotonic() - t_drain
        if drain_s > _DRAIN_LOG_THRESHOLD:
            logger.info("耗时|%s等上轮收尾 %.1fs session=%s", label, drain_s, session_id)
    try:
        yield
    finally:
        usage = usage_tracker.snapshot()
        logger.info(
            "耗时|%s合计 %.1fs（%s）session=%s",
            label, time.monotonic() - started, usage_tracker.fmt(usage), session_id,
        )
        usage_tracker.warn_if_reasoning_dominates(usage)


def classify_llm_error(exc: BaseException) -> str:
    """把底层异常翻成对玩家可行动的一句话；无法归类时返回空串。"""
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code in (401, 403):
            return "鉴权失败，请到设置页检查 API Key 是否正确并已激活"
        if code == 429:
            return "被限流或额度不足，请稍后重试或检查账户额度"
        if code >= 500:
            return "AI 服务端错误，通常稍后重试即可"
    if isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout | httpx.ReadTimeout):
        return "连接 AI 服务失败，请检查网络或设置页的 base_url"
    return ""


class HousekeepingManager:
    """管理每个房间至多一个后台收尾任务，并在下一轮开始前排干。"""

    def __init__(self) -> None:
        self.tasks: dict[str, asyncio.Task] = {}
        self._reported: weakref.WeakSet[asyncio.Task] = weakref.WeakSet()

    async def drain(self, session_id: str) -> None:
        # 等待期间可能又有新收尾接上（链式），一直等到登记的是已完成任务为止。
        while (task := self.tasks.get(session_id)) is not None:
            if not task.done():
                try:
                    # 取消等待者不能取消共享收尾，否则下一轮会读取未完成的世界记忆。
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    if task.done():
                        # 任务自身被取消（进程关停等），不是等待者被取消。
                        self._forget(session_id, task)
                        continue
                    raise
                except Exception:
                    pass
            if task not in self._reported:
                self._report(session_id, task)
            self._forget(session_id, task)

    def _forget(self, session_id: str, task: asyncio.Task) -> None:
        if task.done() and self.tasks.get(session_id) is task:
            self.tasks.pop(session_id, None)

    def _report(self, session_id: str, task: asyncio.Task) -> None:
        """及时读取异常：无人 drain 的会话也不留 “exception was never retrieved”。"""
        if task in self._reported:
            return
        self._reported.add(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("后台收尾失败: session=%s", session_id, exc_info=exc)

    def spawn(
        self,
        session_id: str,
        llm,
        summarize: HousekeepingFn,
        backstage: HousekeepingFn,
    ) -> None:
        previous = self.tasks.get(session_id)

        async def run() -> None:
            from app.database import SessionLocal

            if previous is not None and not previous.done():
                # 未排空就又启动收尾（如不 drain 的入口）：串在旧任务之后，不并发改写同一份世界记忆，
                # 也不覆盖登记导致旧任务无人等待。
                try:
                    await asyncio.shield(previous)
                except asyncio.CancelledError:
                    if not previous.done():
                        raise  # 本任务自身被取消
                except Exception:
                    pass  # 旧任务的异常由其 done 回调记录
            db = SessionLocal()
            started_at = time.monotonic()
            try:
                with usage_tracker.phase("收尾"), call_budget.scope(
                    max_calls=12, max_tokens=100000, seconds=settings.housekeeping_max_seconds,
                ) as budget:
                    await asyncio.wait_for(summarize(db, session_id, llm), budget.remaining_seconds)
                    await asyncio.wait_for(backstage(db, session_id, llm), budget.remaining_seconds)
            except asyncio.TimeoutError:
                db.rollback()
                logger.warning("后台收尾达到时间上限，下轮重试: session=%s", session_id)
            finally:
                db.close()
                elapsed = time.monotonic() - started_at
                if elapsed > 0.1:
                    logger.info("耗时|收尾（后台）%.1fs session=%s", elapsed, session_id)

        task = asyncio.create_task(usage_tracker.tracked(session_id, run()))
        task.add_done_callback(lambda done: self._report(session_id, done))
        task.add_done_callback(lambda done: self._forget(session_id, done))
        self.tasks[session_id] = task
