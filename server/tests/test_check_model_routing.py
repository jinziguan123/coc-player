"""终检/去口癖的模型选择：快模型先跑，结果不可用再回退叙事模型。

这条路径的难点不在「换成快模型」，而在两件事：
1. 没单独配快模型时**不能把同一份请求对同一个模型发两遍**——那只会更慢；
2. 快模型的结果必须过确定性检查（解析失败/改写为空/长度失真/口癖没减少）才采信，
   否则回退叙事模型重跑。这里测的就是这两个边界。
"""

import asyncio
import logging

import pytest

from app.ai import llm_factory, turn_validator
from app.ai.turn_planner import SafetyPolicy, TurnPlan
from app.services import style_editor

# 一处「像是」揣测；CLEAN 是去掉它之后的版本，tic_score 从 1 降到 0。
SHOT = "她抬起头，像是听见了什么。"
CLEAN = "她抬起头，听见了什么。"


class _ScriptedLLM:
    """按顺序返回预设响应；记录调用次数，用来断言「谁被调了、调了几次」。"""

    def __init__(self, *replies, raise_exc=None):
        self.replies = list(replies)
        self.raise_exc = raise_exc
        self.calls = 0
        self.messages = []

    async def complete(self, messages, temperature=0.7, max_tokens=None, response_format=None):
        self.calls += 1
        self.messages.append(messages)
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.replies.pop(0) if self.replies else ""


@pytest.fixture
def fast_configured(monkeypatch):
    """把 check_llm_pair 装成「单独配了快模型」：调用 install(fast_llm) 后返回它。"""
    def _install(fast_llm):
        monkeypatch.setattr(llm_factory.settings, "checks_use_fast_model", True)
        monkeypatch.setattr(llm_factory.profile_store, "load_fast_profile", lambda: object())
        monkeypatch.setattr(llm_factory, "get_fast_llm", lambda: fast_llm)
        return fast_llm
    return _install


@pytest.fixture
def log_records():
    """抓 app 树真正写出去的日志（app 树 propagate=False，caplog 抓不到）。"""
    import app.main  # noqa: F401 — 导入即完成日志配置，否则 app 树继承根 logger 的 WARNING

    records: list[logging.LogRecord] = []

    class _Collect(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger("app")
    handler = _Collect()
    logger.addHandler(handler)
    try:
        yield records
    finally:
        logger.removeHandler(handler)


def _plan() -> TurnPlan:
    return TurnPlan(safety=SafetyPolicy(do_not_reveal=["隐藏真相"]))


# ── check_llm_pair 的三种口径 ───────────────────────────────────────────────


def test_pair_without_fast_profile_runs_main_once(monkeypatch):
    monkeypatch.setattr(llm_factory.settings, "checks_use_fast_model", True)
    monkeypatch.setattr(llm_factory.profile_store, "load_fast_profile", lambda: None)
    main = object()
    assert llm_factory.check_llm_pair(main) == (main, None)


def test_pair_with_fast_profile_returns_fast_then_main(fast_configured):
    fast = fast_configured(object())
    main = object()
    assert llm_factory.check_llm_pair(main) == (fast, main)


def test_pair_respects_toggle_off(fast_configured, monkeypatch):
    fast_configured(object())
    monkeypatch.setattr(llm_factory.settings, "checks_use_fast_model", False)
    main = object()
    assert llm_factory.check_llm_pair(main) == (main, None)


# ── 终检 ────────────────────────────────────────────────────────────────────


def test_validator_uses_fast_model_first(fast_configured):
    fast = fast_configured(_ScriptedLLM('{"violated": false}'))
    main = _ScriptedLLM('{"violated": false}')
    result = asyncio.run(turn_validator.validate_turn_narration(
        main, _plan(), "一段旁白。", turn_inputs="玩家：我去看看。",
    ))
    assert result is not None and result.violated is False
    assert fast.calls == 1 and main.calls == 0


def test_validator_falls_back_when_fast_output_unusable(fast_configured):
    fast = fast_configured(_ScriptedLLM("这不是 JSON"))
    main = _ScriptedLLM('{"violated": false}')
    result = asyncio.run(turn_validator.validate_turn_narration(
        main, _plan(), "一段旁白。", turn_inputs="玩家：我去看看。",
    ))
    assert result is not None and result.violated is False
    assert fast.calls == 1 and main.calls == 1


def test_validator_falls_back_when_violated_without_correction(fast_configured):
    fast = fast_configured(_ScriptedLLM('{"violated": true, "reason": "泄密"}'))
    main = _ScriptedLLM(
        '{"violated": true, "reason": "泄密", "corrected_narration": "改写后的旁白。"}'
    )
    result = asyncio.run(turn_validator.validate_turn_narration(
        main, _plan(), "一段旁白。", turn_inputs="玩家：我去看看。",
    ))
    assert result is not None and result.violated is True
    assert result.corrected_narration == "改写后的旁白。"
    assert fast.calls == 1 and main.calls == 1


def test_validator_falls_back_when_correction_length_distorted(fast_configured):
    fast = fast_configured(_ScriptedLLM(
        '{"violated": true, "corrected_narration": "太短。"}'
    ))
    main = _ScriptedLLM(
        '{"violated": true, "corrected_narration": "这是一段长度正常的改写旁白，保留原意。"}'
    )
    result = asyncio.run(turn_validator.validate_turn_narration(
        main, _plan(), "一段旁白，长度足够用来做比例判断。", turn_inputs="玩家：我去看看。",
    ))
    assert result is not None
    assert result.corrected_narration.startswith("这是一段长度正常")
    assert fast.calls == 1 and main.calls == 1


def test_validator_falls_back_when_fast_misses_verbatim_secret(fast_configured):
    """快模型判「无违规」、但隐藏真相原文整句出现在旁白里——最危险的漏判要兜住。"""
    plan = TurnPlan(safety=SafetyPolicy(do_not_reveal=["管家就是凶手"]))
    fast = fast_configured(_ScriptedLLM('{"violated": false}'))
    main = _ScriptedLLM(
        '{"violated": true, "corrected_narration": "管家站在门口，没有多说。"}'
    )
    result = asyncio.run(turn_validator.validate_turn_narration(
        main, plan, "管家就是凶手，他站在门口。", turn_inputs="玩家：我去看看。",
    ))
    assert result is not None and result.violated is True
    assert fast.calls == 1 and main.calls == 1


def test_validator_toggle_off_keeps_main_only(fast_configured, monkeypatch):
    fast = fast_configured(_ScriptedLLM('{"violated": false}'))
    monkeypatch.setattr(llm_factory.settings, "checks_use_fast_model", False)
    main = _ScriptedLLM('{"violated": false}')
    result = asyncio.run(turn_validator.validate_turn_narration(
        main, _plan(), "一段旁白。", turn_inputs="玩家：我去看看。",
    ))
    assert result is not None
    assert fast.calls == 0 and main.calls == 1


def test_validator_fallback_is_logged(fast_configured, log_records):
    fast_configured(_ScriptedLLM("坏输出"))
    main = _ScriptedLLM('{"violated": false}')
    asyncio.run(turn_validator.validate_turn_narration(
        main, _plan(), "一段旁白。", turn_inputs="玩家：我去看看。",
    ))
    text = " ".join(record.getMessage() for record in log_records)
    assert "耗时|终检 快模型" in text
    assert "回退叙事模型重跑" in text


# ── 去口癖编辑器 ────────────────────────────────────────────────────────────


def test_style_editor_uses_fast_model_first(fast_configured):
    fast = fast_configured(_ScriptedLLM(CLEAN))
    main = _ScriptedLLM(CLEAN)
    assert asyncio.run(style_editor.polish_narration(main, SHOT)) == CLEAN
    assert fast.calls == 1 and main.calls == 0


def test_style_editor_falls_back_when_fast_rewrite_rejected(fast_configured):
    # 快模型把「像是」换成「仿佛」——口癖没减少，_acceptable 拒绝 → 回退叙事模型。
    fast = fast_configured(_ScriptedLLM(SHOT.replace("像是", "仿佛")))
    main = _ScriptedLLM(CLEAN)
    assert asyncio.run(style_editor.polish_narration(main, SHOT)) == CLEAN
    assert fast.calls == 1 and main.calls == 1


def test_style_editor_falls_back_when_fast_call_raises(fast_configured):
    fast = fast_configured(_ScriptedLLM(raise_exc=RuntimeError("boom")))
    main = _ScriptedLLM(CLEAN)
    assert asyncio.run(style_editor.polish_narration(main, SHOT)) == CLEAN
    assert fast.calls == 1 and main.calls == 1


def test_style_editor_toggle_off_uses_main_once(fast_configured, monkeypatch):
    fast = fast_configured(_ScriptedLLM(CLEAN))
    monkeypatch.setattr(llm_factory.settings, "checks_use_fast_model", False)
    main = _ScriptedLLM(CLEAN)
    assert asyncio.run(style_editor.polish_narration(main, SHOT)) == CLEAN
    assert fast.calls == 0 and main.calls == 1


def test_style_editor_no_tic_calls_nobody(fast_configured):
    fast = fast_configured(_ScriptedLLM(CLEAN))
    main = _ScriptedLLM(CLEAN)
    assert asyncio.run(style_editor.polish_narration(main, CLEAN)) is None
    assert fast.calls == 0 and main.calls == 0


def test_style_editor_fallback_is_logged(fast_configured, log_records):
    fast_configured(_ScriptedLLM(SHOT.replace("像是", "仿佛")))
    main = _ScriptedLLM(CLEAN)
    asyncio.run(style_editor.polish_narration(main, SHOT))
    text = " ".join(record.getMessage() for record in log_records)
    assert "耗时|去口癖 快模型" in text
    assert "回退叙事模型重跑" in text
