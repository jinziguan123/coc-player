"""应用日志配置：保证 logger.info 真的会输出。

此前全项目没有任何日志配置，于是走 Python 默认值（根 logger = WARNING、无 handler），
后端 27 处 logger.info 一条都没输出过——包括各环节的「耗时|…」埋点。查性能问题时会
误以为「日志里没有就是没发生」，比没有埋点更糟。
"""

import logging


def test_app_logger_emits_info():
    import app.main  # noqa: F401 — 导入即完成日志配置

    logger = logging.getLogger("app.services.turn_orchestrator")
    assert logger.isEnabledFor(logging.INFO), "app.* 的 INFO 必须可输出，否则耗时埋点等于没有"


def test_app_logger_has_handler_and_does_not_propagate():
    """自带 handler 才能真的写出去；不冒泡到根，避免与 uvicorn 的 handler 重复打印。"""
    import app.main  # noqa: F401

    app_logger = logging.getLogger("app")
    assert app_logger.handlers, "app 树必须自带 handler"
    assert app_logger.propagate is False


def test_timing_marks_are_actually_logged(caplog):
    """回合各环节的耗时埋点要能被捕获到——这是排查「为什么这么慢」的唯一依据。"""
    import app.main  # noqa: F401

    logger = logging.getLogger("app.services.turn_orchestrator")
    with caplog.at_level(logging.INFO, logger="app.services.turn_orchestrator"):
        logger.info("耗时|planner %.1fs session=%s", 1.5, "s1")
    assert any("耗时|planner" in r.message for r in caplog.records)
