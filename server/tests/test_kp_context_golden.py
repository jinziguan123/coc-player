"""``build_kp_context`` 的金标准（characterization）测试。

与 ``test_narration_protocol_golden`` 同一性质：断言的不是「什么才对」，而是
「当前装配就是这样」。理由也一样——上下文装配决定了 KP 好不好用，却是全项目最难改的
一处（近 500 行、二十来个小节各带自己的注入条件）。这里给它一张网：

1. **快照比对**（``fixtures/kp_context_golden.json``）逐用例钉住 role 序列与每条消息的
   全文，任何小节的悄悄增删都会被照出来；
2. **小节存在性**断言：把「哪些局面下哪些小节必须在/必须不在」写成人读得懂的用例，
   这类断言比快照更耐得住无关改动，也更能说明设计意图。

**快照怎么更新**：只在**有意**改变装配时更新，且必须说明改了哪几条。
重新生成：``.venv/bin/python -m tests.regen_kp_context_golden``。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ai.context import build_kp_context
from tests._kp_context_corpus import CASES, render

GOLDEN = json.loads(
    (Path(__file__).parent / "fixtures" / "kp_context_golden.json").read_text("utf-8")
)



def _text(kwargs: dict) -> str:
    return "\n".join(str(m.get("content") or "") for m in build_kp_context(**kwargs))


@pytest.mark.parametrize("name,kwargs", CASES, ids=[c[0] for c in CASES])
def test_装配与金标准一致(name, kwargs):
    assert name in GOLDEN, f"语料新增了用例 {name!r}，请重新生成金标准快照"
    assert render(kwargs) == GOLDEN[name]


def test_金标准覆盖了全部语料():
    assert set(GOLDEN) == {c[0] for c in CASES}


# ── 小节存在性：写成意图，而不是快照 ──────────────────────────────

def _case(name: str) -> dict:
    return dict(CASES)[name]


def test_身份与裁定手册恒在():
    """静态段是缓存命中的主体，任何局面下都不该被裁掉。"""
    for name in ("常规回合", "开场无事件", "战斗进行中"):
        text = _text(_case(name))
        assert "守秘人" in text or "KP" in text
        assert "裁定" in text


def test_开场不给线索与幕后():
    """开场隔离：防 KP 拿「待发现」的线索现编进开场白。"""
    opening = _text(_case("开场无事件"))
    normal = _text(_case("常规回合"))
    assert "线索台账" in normal
    assert "线索台账" not in opening


def test_队伍位置在非开场且给了分组时注入():
    """位置小节是「这一轮该演谁、演在哪」的前提。

    注意它由调用方传入的 ``scene_groups`` 驱动（turn_orchestrator 用 _location_groups
    确定性归并后传进来）——不传就没有这一节，这正是那次《鬼屋》故障的形态。
    开场时还没人动过，即便给了分组也不注入。
    """
    assert "队伍此刻的位置" in _text(_case("全队同处一地"))
    assert "队伍此刻的位置" in _text(_case("分头行动逐组构建上下文"))
    assert "队伍此刻的位置" not in _text(_case("开场无事件"))


def test_全队同处一地时点破意图不等于已分头():
    """「说要分头」不等于「已经分头」——位置只在真的移动后才变。"""
    text = _text(_case("全队同处一地"))
    assert "全队都在一起" in text


def test_能力广告按本场配置开合():
    """没挂规则书就不该广告 [RULE_LOOKUP]——广告了只会诱导 KP 发无效指令。"""
    assert "RULE_LOOKUP" not in _text(_case("常规回合"))
    assert "RULE_LOOKUP" in _text(_case("挂了规则书就广告查阅能力"))
    assert "MODULE_LOOKUP" not in _text(_case("常规回合"))
    assert "MODULE_LOOKUP" in _text(_case("建了模组索引就广告原文检索"))


def test_回想能力只在摘要真的推进过游标时广告():
    """局刚开始时全部历史都还逐字躺在上下文里，广告了只会诱导 KP 去查眼前就有的东西。"""
    assert "RECALL_HISTORY" not in _text(_case("常规回合"))
    assert "RECALL_HISTORY" in _text(_case("滚动摘要已推进游标就广告回想能力"))


def test_战斗进行中给出硬约束():
    """战斗期间主线 KP 不能把冲突「讲完」——否则和下一轮引擎结算当场对撞。"""
    text = _text(_case("战斗进行中"))
    assert "战斗进行中" in text
    assert "绝不能" in text
    assert "战斗进行中" not in _text(_case("常规回合"))


def test_分头时以本组场景为锚():
    """每列以自身所在场景构建上下文，否则各列都拿到主角场景的资料、重复叙述同一场景。"""
    text = _text(_case("分头行动逐组构建上下文"))
    assert "当铺" in text          # viewer_scene_id=s_shop
    assert "队伍此刻的位置" in text


def test_村规与文风进上下文():
    assert "本桌规矩" in _text(_case("村规与桌面约定"))
    assert "本桌规矩" not in _text(_case("常规回合"))


def test_被动摘录独立成节():
    assert "渡口的雾一年到头不散" in _text(_case("被动注入的模组原文摘录"))
    assert "困难难度" in _text(_case("被动注入的规则要点"))


def test_幕后真相只给_KP():
    """模组真相带守密措辞进上下文；它是 KP 专属，绝不能出现在玩家可见产物里。"""
    assert "摆渡人二十年前就淹死了" in _text(_case("常规回合"))
