"""临时疯狂发作的骰面记录：症状与时长都是 1D10，必须留下可展示的点数。"""

import random

from app.rules.coc.components import madness


def test_make_bout_records_symptom_and_duration_rolls(monkeypatch):
    monkeypatch.setattr(random, "randint", lambda a, b: 7)
    bout = madness.make_bout()
    table = madness.BOUT_SYMPTOMS
    assert bout["symptom_roll"] == 7
    assert bout["label"] == table[6]["label"]
    assert bout["duration_roll"] == 7
    assert bout["turns_left"] == 7


def test_make_bout_rolls_are_in_range():
    for _ in range(20):
        bout = madness.make_bout()
        assert 1 <= bout["symptom_roll"] <= len(madness.BOUT_SYMPTOMS)
        assert 1 <= bout["duration_roll"] <= 10
        assert bout["turns_left"] == bout["duration_roll"]
