"""叙事文本回显与近重复检测的纯函数。"""

from __future__ import annotations

import re
from difflib import SequenceMatcher


_IGNORED_RE = re.compile(r"[\s\W_]+", re.UNICODE)
_PARAGRAPH_RE = re.compile(r"\n\s*\n+")


def normalize_comparison_text(text: str) -> str:
    """移除空白、标点和 Markdown 装饰，仅保留可比较的文字数字。"""
    return _IGNORED_RE.sub("", (text or "").casefold())


def is_near_duplicate(
    text: str,
    candidates: list[str] | tuple[str, ...],
    *,
    min_chars: int = 20,
    threshold: float = 0.88,
) -> bool:
    """判断文本是否与候选之一相同或高度相似；短文本只接受完全相同。"""
    normalized = normalize_comparison_text(text)
    if not normalized:
        return False
    for candidate in candidates:
        other = normalize_comparison_text(candidate)
        if not other:
            continue
        if normalized == other:
            return True
        if min(len(normalized), len(other)) < min_chars:
            continue
        length_ratio = min(len(normalized), len(other)) / max(len(normalized), len(other))
        if length_ratio < 0.68:
            continue
        if (normalized in other or other in normalized) and length_ratio >= 0.78:
            return True
        if SequenceMatcher(None, normalized, other, autojunk=False).ratio() >= threshold:
            return True
    return False


def comparable_passages(text: str, *, min_chars: int = 20) -> list[str]:
    """提取适合做近重复比较的非空段落。"""
    return [
        passage.strip()
        for passage in _PARAGRAPH_RE.split(text or "")
        if len(normalize_comparison_text(passage)) >= min_chars
    ]


def has_near_duplicate_passages(text: str) -> bool:
    """回复内部是否存在明显的近重复段落。"""
    seen: list[str] = []
    for passage in comparable_passages(text, min_chars=24):
        if is_near_duplicate(passage, seen, min_chars=24, threshold=0.86):
            return True
        seen.append(passage)
    return False
