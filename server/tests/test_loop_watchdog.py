"""事件循环看门狗：被同步代码卡住时记下卡住它的代码栈。"""

import asyncio
import logging
import time

from app.services import loop_watchdog


def _block_the_loop():
    time.sleep(0.6)   # 模拟 async 端点里的同步重活


def test_watchdog_reports_blocking_stack(caplog):
    async def main():
        stop = loop_watchdog.start(asyncio.get_running_loop(), stall_seconds=0.3)
        await asyncio.sleep(0.2)
        _block_the_loop()
        await asyncio.sleep(0.6)
        stop.set()

    with caplog.at_level(logging.WARNING, logger="app.services.loop_watchdog"):
        asyncio.run(main())
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "事件循环已被卡住" in text and "_block_the_loop" in text
    assert "事件循环恢复" in text


def test_watchdog_quiet_when_loop_is_free(caplog):
    async def main():
        stop = loop_watchdog.start(asyncio.get_running_loop(), stall_seconds=0.3)
        for _ in range(8):
            await asyncio.sleep(0.1)
        stop.set()

    with caplog.at_level(logging.WARNING, logger="app.services.loop_watchdog"):
        asyncio.run(main())
    assert not caplog.records
