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


def test_usage_delta_and_format():
    """阶段用量 = 前后两次 snapshot 之差。

    比读 provider 的 last_usage 准：一个阶段里往往不止一次调用（工具轮次、校验重写、
    并行的队友决策），last_usage 只剩最后一次，差值才是这个阶段真正花掉的。
    """
    from app.ai import usage_tracker

    before = {"prompt_tokens": 1000, "completion_tokens": 400, "total_tokens": 1400, "calls": 1}
    after = {"prompt_tokens": 46200, "completion_tokens": 18700, "total_tokens": 64900, "calls": 4}
    d = usage_tracker.delta(before, after)
    assert d["calls"] == 3 and d["prompt_tokens"] == 45200 and d["completion_tokens"] == 18300
    assert usage_tracker.fmt(d) == "3 次调用，入 45.2k / 出 18.3k"


def test_usage_delta_handles_empty_accumulator():
    """脱离生成上下文时累加器为空，取差不能炸。"""
    from app.ai import usage_tracker

    zero = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}
    assert usage_tracker.delta(zero, zero)["calls"] == 0
    assert "0 次调用" in usage_tracker.fmt(usage_tracker.delta(zero, zero))
