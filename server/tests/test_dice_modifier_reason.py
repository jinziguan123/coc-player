"""奖惩骰要标出**凭什么**。

奖惩骰实打实地改了成败概率。只显示「惩罚骰 ×1」而不说来由，玩家除了猜自己是不是被
针对了没有别的办法——尤其战斗里的那些，全是几何算出来的，玩家在战场上看不到算式。
"""

import pytest

from app.services import dice_runtime


class _Result:
    roll, tens, tens_kept, units = 35, [30, 60], 30, 5
    bonus, penalty = 0, 1


def test_prompt_text_states_reason_before_the_roll():
    """待投骰提示里就要写明——那是玩家**投骰之前**唯一能看到的说明。

    结果卡上的标注要等骰子落定才出现，那时再解释「你刚才吃了个惩罚骰」已经晚了。
    """
    text = dice_runtime._check_prompt_text(
        "江户川龙牙", "侦查", "normal", 0, 1, "车厢里几乎全黑",
    )
    assert "惩罚骰 ×1" in text and "车厢里几乎全黑" in text


def test_prompt_text_unchanged_without_modifiers():
    """没有奖惩骰时提示语一字不变（存量行为）。"""
    assert dice_runtime._check_prompt_text("A", "侦查", "normal") == "请 A 进行一次「侦查」检定"
    assert dice_runtime._check_prompt_text("A", "侦查", "hard", 0, 0, "无关理由").endswith("检定")


def test_prompt_text_marks_count_even_without_reason():
    """KP 没给理由也要标出数量——玩家至少该知道这一掷被动了手脚。"""
    text = dice_runtime._check_prompt_text("A", "侦查", "normal", 1, 0, "")
    assert "奖励骰 ×1" in text
    assert "：" not in text.split("（")[-1]     # 不留一个空冒号


@pytest.mark.parametrize(
    ("bonus", "penalty", "reason", "expected"),
    [
        (1, 0, "手电筒照明充足", [{"kind": "bonus", "n": 1, "reason": "手电筒照明充足"}]),
        (0, 2, "肩上的伤在流血", [{"kind": "penalty", "n": 2, "reason": "肩上的伤在流血"}]),
        (0, 0, "无奖惩", []),
        (1, 0, "   ", []),      # 理由为空 → 不造条目，绝不替 KP 编一个
        (0, 0, "", []),
    ],
)
def test_modifier_notes(bonus, penalty, reason, expected):
    assert dice_runtime.modifier_notes(bonus, penalty, reason) == expected


def test_dice_detail_omits_key_when_no_modifiers():
    """没有来由时整个键不出现：旧事件与旧前端行为逐字不变。"""
    detail = dice_runtime._check_dice_detail(_Result())
    assert "modifiers" not in detail
    with_mods = dice_runtime._check_dice_detail(
        _Result(), [{"kind": "penalty", "n": 1, "reason": "黑"}],
    )
    assert with_mods["modifiers"] == [{"kind": "penalty", "n": 1, "reason": "黑"}]


def test_combat_geometry_reports_why(monkeypatch):
    """战斗几何算出的奖惩骰要带来由：超程 / 半掩体 / 抵近，玩家看不到算式。"""
    from app.services import combat_service

    grid = {"cell_m": 1.5, "cover": {"3,0": "half"}}
    state = {"grid": grid}
    actor = {"pos": {"x": 0, "y": 0}}
    target = {"pos": {"x": 6, "y": 0}}
    # 手枪基础射程折成格数后 6 格属于超程但可及
    monkeypatch.setattr(combat_service.positioning, "range_in_cells", lambda *a, **k: 3)
    monkeypatch.setattr(combat_service.positioning, "has_line_of_sight", lambda *a, **k: True)

    _b, penalty, reachable, _why, notes = combat_service._attack_geometry(
        state, actor, target, "手枪", True,
    )
    assert reachable and penalty >= 1
    reasons = [n["reason"] for n in notes]
    assert "超出武器基础射程" in reasons
    assert "目标有半掩体遮挡" in reasons


def test_combat_geometry_without_grid_is_silent():
    """无方格战斗（旧战斗态）没有奖惩骰，也就没有来由——行为与从前一致。"""
    from app.services import combat_service

    b, p, reachable, why, notes = combat_service._attack_geometry(
        {}, {}, {}, "手枪", True,
    )
    assert (b, p, reachable, why, notes) == (0, 0, True, "", [])
