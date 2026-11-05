"""骰子表达式解析：固定值与骰混排（模组原文常见 5+1d5）。"""

import random

import pytest

from app.rules.dice import roll


def test_mixed_fixed_plus_dice(monkeypatch):
    monkeypatch.setattr(random, "randint", lambda a, b: 3)
    result = roll("5+1d5")
    assert result.rolls == [3]
    assert result.modifier == 5
    assert result.total == 8


def test_dice_plus_fixed(monkeypatch):
    monkeypatch.setattr(random, "randint", lambda a, b: 4)
    result = roll("1d5+5")
    assert result.total == 9 and result.modifier == 5


def test_negative_modifier(monkeypatch):
    monkeypatch.setattr(random, "randint", lambda a, b: 6)
    result = roll("2d6-1")
    assert result.rolls == [6, 6]
    assert result.total == 11
    assert result.modifier == -1


def test_omitted_dice_count(monkeypatch):
    monkeypatch.setattr(random, "randint", lambda a, b: 2)
    result = roll("d5")
    assert result.rolls == [2]
    assert result.total == 2


def test_invalid_expressions():
    for bad in ("", "abc", "1d", "++1", "1d6+"):
        with pytest.raises(ValueError):
            roll(bad)
