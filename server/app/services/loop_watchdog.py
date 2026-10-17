"""事件循环看门狗：循环被同步代码卡住超过阈值时，把卡住它的那段代码栈写进日志。

单进程的 asyncio 服务里，任何一个 async 端点或后台任务里的同步重活（解析 PDF、处理图片、
同步网络调用……）都会让**所有**请求一起等。这种卡顿从外面看只是「接口都不返回了」，很难定位。

做法：事件循环里每 0.1s 打一次心跳；一条守护线程盯着心跳，超过阈值没跳就用
``sys._current_frames()`` 抓事件循环线程此刻的栈——那正是卡住它的代码。每次卡顿只报一回，
恢复时再补一条总时长。只读、不干预，开销可以忽略。
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
import traceback

logger = logging.getLogger(__name__)

#: 卡住多久才报（秒）。正常的 await 切换远低于它；超过就是有同步活霸着循环。
STALL_SECONDS = 1.0
_BEAT_SECONDS = 0.1


def start(loop: asyncio.AbstractEventLoop, *, stall_seconds: float = STALL_SECONDS) -> threading.Event:
    """在给定循环上启动看门狗，返回一个 Event，set 它即停止。须在事件循环线程里调用。"""
    loop_thread = threading.get_ident()
    state = {"beat": time.monotonic()}
    stop = threading.Event()

    def beat() -> None:
        state["beat"] = time.monotonic()
        if not stop.is_set():
            loop.call_later(_BEAT_SECONDS, beat)

    loop.call_soon(beat)

    def watch() -> None:
        reported_at: float | None = None
        while not stop.wait(0.25):
            stalled = time.monotonic() - state["beat"]
            if stalled > stall_seconds and reported_at is None:
                reported_at = state["beat"]
                frame = sys._current_frames().get(loop_thread)
                stack = "".join(traceback.format_stack(frame)[-12:]) if frame else "（取不到栈）"
                logger.warning("事件循环已被卡住 %.1fs，此刻在执行：\n%s", stalled, stack)
            elif reported_at is not None and state["beat"] > reported_at:
                logger.warning("事件循环恢复，这次共卡住约 %.1fs", state["beat"] - reported_at)
                reported_at = None

    threading.Thread(target=watch, name="loop-watchdog", daemon=True).start()
    return stop
