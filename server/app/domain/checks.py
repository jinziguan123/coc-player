"""检定通用规则：只比较技能名等业务值，不读写存档。"""
import re


def normalize_skill(skill: str) -> str:
    return re.sub(r"\s+", "", str(skill or "")).lower()


def skill_matches(expected: str, rolled: str) -> bool:
    """暂存时要求的技能与实际投出的技能是否对得上：忽略空白和大小写，互相包含即算。"""
    a, b = normalize_skill(expected), normalize_skill(rolled)
    return bool(a and b) and (a in b or b in a)
