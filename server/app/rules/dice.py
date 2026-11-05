import random
import re
from dataclasses import dataclass, field


@dataclass
class DiceRollResult:
    notation: str
    rolls: list[int] = field(default_factory=list)
    modifier: int = 0
    total: int = 0


#: 单项：可选符号 + 骰式（数量可省略）或固定值。
_TERM_RE = re.compile(r"(?P<sign>[+-]?)(?:(?P<count>\d*)d(?P<sides>\d+)|(?P<flat>\d+))")
#: 整条表达式：固定值与骰式可以任意加减混排（模组原文常见「5+1d5」这类写法）。
_EXPR_RE = re.compile(r"^[+-]?(?:\d*d\d+|\d+)(?:[+-](?:\d*d\d+|\d+))*$")


def roll(notation: str) -> DiceRollResult:
    """解析并执行骰子表达式。

    除了常规的 '2d6+3'、'1d100'、'3d6'，也支持模组原文里「固定值 + 骰」的写法：
    '5+1d5'、'1d5+5'、'1d6-1'、'd5'（省略数量即 1 颗）。多项加减会合并求和。
    """
    text = re.sub(r"\s+", "", (notation or "").strip().lower())
    if not text or not _EXPR_RE.match(text):
        raise ValueError(f"无效的骰子表达式: {notation}")

    rolls: list[int] = []
    modifier = 0
    for match in _TERM_RE.finditer(text):
        negative = match.group("sign") == "-"
        if match.group("sides"):
            count = int(match.group("count") or 1)
            sides = int(match.group("sides"))
            if negative or count <= 0 or sides <= 0:
                raise ValueError(f"无效的骰子表达式: {notation}")
            rolls.extend(random.randint(1, sides) for _ in range(count))
        else:
            value = int(match.group("flat"))
            modifier += -value if negative else value

    return DiceRollResult(
        notation=notation,
        rolls=rolls,
        modifier=modifier,
        total=sum(rolls) + modifier,
    )


def roll_percentile() -> int:
    return random.randint(1, 100)


@dataclass
class PercentileDetail:
    """一次 d100 检定的逐骰明细，供前端 3D 骰子动画严格还原。

    CoC d100 = 十位骰（0/10/…/90）+ 个位骰（0-9），十位00+个位0 视作 100。
    奖励骰：额外多掷 N 个十位、取最小（最有利）；惩罚骰：多掷 N 个、取最大（最不利）；
    两者互相抵消，净值决定加掷几个十位、以及取优还是取劣。
    """
    result: int          # 最终 d100（1-100）
    tens: list[int]      # 所有掷出的十位（每个 ∈ {0,10,…,90}），含常规 1 个 + 净奖惩加掷的 N 个
    tens_kept: int       # 最终采用的十位
    units: int           # 个位骰（0-9）


def compose_d100(tens_kept: int, units: int) -> int:
    """由采用的十位与个位合成 d100：十位00+个位0 视作 100。"""
    val = tens_kept + units
    return 100 if val == 0 else val


def decompose_d100(d100: int) -> tuple[int, int]:
    """把一个 d100（1-100）拆成 (十位, 个位)：100 → (0, 0)，45 → (40, 5)，5 → (0, 5)。"""
    d = d100 % 100  # 100 → 0
    return (d // 10) * 10, d % 10


def roll_percentile_detailed(bonus: int = 0, penalty: int = 0) -> PercentileDetail:
    """掷一次带奖励/惩罚骰的 d100，返回逐骰明细。

    净奖惩 = bonus - penalty：>0 多掷 |净| 个十位并取最小（最有利）；<0 多掷并取最大
    （最不利）；=0 只掷 1 个十位（等同常规 roll_percentile 的行为）。个位骰始终只掷一次。
    """
    net = int(bonus) - int(penalty)
    extra = abs(net)
    units = random.randint(0, 9)
    tens = [random.randint(0, 9) * 10 for _ in range(extra + 1)]
    tens_kept = min(tens) if net > 0 else (max(tens) if net < 0 else tens[0])
    return PercentileDetail(
        result=compose_d100(tens_kept, units),
        tens=tens,
        tens_kept=tens_kept,
        units=units,
    )
