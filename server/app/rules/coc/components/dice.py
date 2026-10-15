"""coc7.dice：骰式解析与 d100。实现是规则系统无关的 :mod:`app.rules.dice`，这里只声明组件。

槽位 ``rs.dice``。
"""

from __future__ import annotations

from app.rules import dice
from app.rules.kernel import Component, Provide

COMPONENT = Component(
    id="coc7.dice", title="骰子",
    provides={"dice": Provide(dice, plain=("roll", "roll_percentile"))},
)
