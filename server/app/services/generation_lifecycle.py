"""生成错误分类与后台收尾任务生命周期。"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable

import httpx

from app.ai import usage_tracker

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

    async def drain(self, session_id: str) -> None:
        task = self.tasks.pop(session_id, None)
        if task is not None and not task.done():
            try:
                await task
            except BaseException:
                pass

    def spawn(
        self,
        session_id: str,
        llm,
        summarize: HousekeepingFn,
        backstage: HousekeepingFn,
    ) -> None:
        async def run() -> None:
            from app.database import SessionLocal

            db = SessionLocal()
            started_at = time.monotonic()
            try:
                await summarize(db, session_id, llm)
                await backstage(db, session_id, llm)
            finally:
                db.close()
                elapsed = time.monotonic() - started_at
                if elapsed > 0.1:
                    logger.info("耗时|收尾（后台）%.1fs session=%s", elapsed, session_id)

        self.tasks[session_id] = asyncio.create_task(
            usage_tracker.tracked(session_id, run()),
        )
