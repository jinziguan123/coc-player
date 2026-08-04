"""应用日志配置：保证 logger.info 真的会输出。

此前全项目没有任何日志配置，于是走 Python 默认值（根 logger = WARNING、无 handler），
后端 27 处 logger.info 一条都没输出过——包括各环节的「耗时|…」埋点。查性能问题时会
误以为「日志里没有就是没发生」，比没有埋点更糟。
"""

import logging

import pytest


@pytest.fixture
def app_log():
    """抓 app 树真正写出去的日志。

    不能用 pytest 的 caplog：它把 handler 挂在根 logger 上，而 app 树是 propagate=False
    （防止与 uvicorn 的 handler 重复打印），记录压根不冒泡到根。直接往 app 上挂一个
    handler，测的才是线上真正走的那条路径。
    """
    import app.main  # noqa: F401 — 导入即完成日志配置

    records: list[logging.LogRecord] = []

    class _Collect(logging.Handler):
        def emit(self, record):
            records.append(record)

    app_logger = logging.getLogger("app")
    handler = _Collect()
    app_logger.addHandler(handler)
    try:
        yield records
    finally:
        app_logger.removeHandler(handler)


def _text(records) -> str:
    return " ".join(r.getMessage() for r in records)


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


def test_timing_marks_are_actually_logged(app_log):
    """回合各环节的耗时埋点要能被真的写出去——这是排查「为什么这么慢」的唯一依据。"""
    logging.getLogger("app.services.turn_orchestrator").info(
        "耗时|planner %.1fs session=%s", 1.5, "s1")
    assert "耗时|planner 1.5s" in _text(app_log)


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


def test_reasoning_tokens_are_tracked_separately():
    """思考 token 要单独记：它计入 completion_tokens，但内容被 complete() 丢弃。

    不拆开看就分不清「模型话多」（要改提示词）和「模型在空想」（把思考等级调低即可），
    两者的解法完全不同。实测 planner 一次吐 5.4k 输出，正是靠这一项才判得出成因。
    """
    from app.ai import usage_tracker

    before = usage_tracker._zero()
    after = {
        "prompt_tokens": 8000, "completion_tokens": 5400,
        "reasoning_tokens": 4600, "total_tokens": 13400, "calls": 1,
    }
    assert "思考 4.6k" in usage_tracker.fmt(usage_tracker.delta(before, after))


def test_reasoning_tokens_parsed_from_completion_details(monkeypatch):
    """服务端把 reasoning_tokens 挂在 completion_tokens_details 下（OpenAI/DeepSeek 同构）。"""
    import contextvars

    from app.ai import usage_tracker

    ctx = contextvars.copy_context()

    def _run():
        usage_tracker._acc.set(usage_tracker._zero())
        usage_tracker.add({
            "prompt_tokens": 100, "completion_tokens": 900, "total_tokens": 1000,
            "completion_tokens_details": {"reasoning_tokens": 700},
        })
        return usage_tracker.snapshot()

    snap = ctx.run(_run)
    assert snap["reasoning_tokens"] == 700
    assert snap["completion_tokens"] == 900   # 思考含在总输出里，不是额外的


def test_no_reasoning_detail_means_zero():
    """普通模型不返回这项，不能因此报错或算成缺失。"""
    import contextvars

    from app.ai import usage_tracker

    def _run():
        usage_tracker._acc.set(usage_tracker._zero())
        usage_tracker.add({"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30})
        return usage_tracker.snapshot()

    snap = contextvars.copy_context().run(_run)
    assert snap["reasoning_tokens"] == 0
    assert "思考" not in usage_tracker.fmt(snap)


def test_warns_when_reasoning_dominates_output(app_log):
    """思考占输出大头就提醒，并指向**换快模型**这个真正有效的做法。

    没有这条提醒，「跑一个回合等两分钟」得靠翻日志、比对多轮 token 用量才查得出来。
    """
    from app.ai import usage_tracker

    usage_tracker.warn_if_reasoning_dominates(
        {"completion_tokens": 8300, "reasoning_tokens": 7100},
    )
    msg = _text(app_log)
    assert "86%" in msg
    assert "快模型" in msg            # 指向真正有效的做法


def test_warning_does_not_recommend_tuning_reasoning_effort(app_log):
    """别再推荐调思考等级——实测那是反效果。

    deepseek-v4-flash：不下发 reasoning_effort 时思考中位数 73 token，下发 low 是 391、
    minimal 是 437（各 5 次，区间不重叠）。下发该参数反而想得更多，值本身几乎不起作用。
    """
    from app.ai import usage_tracker

    usage_tracker.warn_if_reasoning_dominates(
        {"completion_tokens": 8300, "reasoning_tokens": 7100},
    )
    msg = _text(app_log)
    assert "填 minimal" not in msg and "填 low" not in msg


def test_no_warning_when_reasoning_is_minor(app_log):
    """思考很少时不该刷屏——偶尔想得多是正常的。"""
    from app.ai import usage_tracker

    usage_tracker.warn_if_reasoning_dominates(
        {"completion_tokens": 8300, "reasoning_tokens": 300},
    )
    assert not app_log


def test_no_warning_without_output():
    """没有输出（调用失败等）不能除零。"""
    from app.ai import usage_tracker

    usage_tracker.warn_if_reasoning_dominates({"completion_tokens": 0, "reasoning_tokens": 0})
