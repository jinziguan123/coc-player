"""事后去口癖：落库前把旁白里带口癖的句子改成直陈。

事前纠偏（context.py 的「文风纠偏」）靠模型自觉。小模型对判断题不服从：鬼屋一局里纠偏
注入了四条，否定对比（词汇级禁令）当轮归零，比喻（「只留给说不清的东西」）反而从 3.3 涨到
10/千字。这一层不靠自觉：流式播完之后、落库之前，把带口癖的句子挑出来，让模型**只改这几句、
其余逐字保留**。前端收到 done 会用落库版本替换流式文本（GameSessionPage 的 resyncHistory），
校验器改写落库版本就是这么生效的，所以玩家最终看到的是改过的。

判据与 turn_validator 共用同一套正则（比喻、否定对比、破折号补充、含混指代、口癖目录），
不另写一份。

fail-open：没有口癖不调用；调用失败、改完口癖没减少、长度失真、凭空多出标记 → 保留原文。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

from app.ai.turn_validator import (
    _ANTITHESIS_RE, _SIMILE_RE, _VAGUE_RE, TIC_CATALOG,
)

logger = logging.getLogger(__name__)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？])|\n")
_DASH = "——"
# 只用目录里按次数判的那些（议论文腔）；引号、顿号按密度判，单句上没意义。
_CATALOG_COUNT_TICS = tuple(t for t in TIC_CATALOG if not t.by_density)

#: 改写后的长度须落在原文的这个区间内，否则视为模型没照规矩来（缩写、扩写、答非所问）。
_LEN_RATIO = (0.6, 1.25)

EDITOR_SYSTEM_PROMPT = (
    "你是一位文字编辑。下面是一段跑团旁白，另附其中带口癖的句子。口癖指："
    "比喻与揣测（像、像是、仿佛、如同、宛如）；否定对比（不是A，而是B）；破折号后追加的解释；"
    "含混指代（某种、有什么东西）。\n"
    "只改标出的句子，其余句子逐字保留。改法：删掉比喻和揣测，只留角色确实看到、听到、摸到的现象；"
    "若比喻承载了线索，把线索写成可观察的具体现象（声音、形状、方位、变化）；"
    "破折号后的内容编进句子或另起一句；否定对比只说是什么；「某种」「什么东西」换成具体的名物，换不出就删。"
    "不加新内容，不增删段落，不改人名与专名，不解释，不加引号或括号。只输出改好的全文。"
)


def tic_score(text: str) -> int:
    """一段文本里口癖标记的总数，用来判断改写是否真的减少了口癖。"""
    body = text or ""
    n = len(_SIMILE_RE.findall(body)) + len(_ANTITHESIS_RE.findall(body))
    n += len(_VAGUE_RE.findall(body)) + body.count(_DASH)
    n += sum(len(t.pattern.findall(body)) for t in _CATALOG_COUNT_TICS)
    return n


def flagged_sentences(text: str) -> list[str]:
    """挑出带口癖的句子（按句号/问叹号/换行切分）。"""
    out: list[str] = []
    for s in _SENTENCE_SPLIT_RE.split(text or ""):
        s = s.strip()
        if s and tic_score(s):
            out.append(s)
    return out


def _clean_output(raw: str) -> str:
    text = (raw or "").strip()
    # 去掉可能包上来的代码栏和「改写后：」一类前缀
    text = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", text).strip()
    text = re.sub(r"^(?:改写后|改好的全文|修改后)\s*[:：]\s*", "", text).strip()
    return text


def _acceptable(old: str, new: str) -> bool:
    if not new:
        return False
    lo, hi = _LEN_RATIO
    ratio = len(new) / max(1, len(old))
    if not (lo <= ratio <= hi):
        return False
    if tic_score(new) >= tic_score(old):
        return False
    # 原文没有方括号标记，改写也不该凭空多出来（模型有时会把指令格式学进去）
    if "[" not in old and "[" in new:
        return False
    return True


async def polish_narration(
    llm, narration: str, *, on_start: Callable[[], None] | None = None,
) -> str | None:
    """返回去掉口癖的旁白；不需要改或改不好时返回 None（调用方保留原文）。"""
    flagged = flagged_sentences(narration)
    if not flagged:
        return None
    if on_start is not None:
        on_start()
    listing = "\n".join(f"- {s}" for s in flagged)
    messages = [
        {"role": "system", "content": EDITOR_SYSTEM_PROMPT},
        {"role": "user", "content": f"【旁白全文】\n{narration}\n\n【带口癖的句子】\n{listing}"},
    ]
    try:
        raw = await llm.complete(messages, temperature=0)
    except Exception:
        logger.exception("去口癖编辑器调用失败，保留原文")
        return None
    new = _clean_output(raw)
    if not _acceptable(narration, new):
        logger.info("去口癖改写未通过校验，保留原文（原 %d 处 → 改后 %d 处，长度比 %.2f）",
                    tic_score(narration), tic_score(new), len(new) / max(1, len(narration)))
        return None
    return new


async def polish_result(
    llm, result: list, event_order: list | None = None,
    *, on_start: Callable[[], None] | None = None,
) -> bool:
    """就地替换 result[0]（落库/展示用的旁白）并重映射交错偏移；返回是否改了。

    ``llm`` 可以是 Provider，也可以是包着它的 agent（KPAgent.llm）。任何异常都吞掉：
    这一层只负责文风，绝不能让一轮叙事因为它落不了库。
    """
    try:
        provider = llm if hasattr(llm, "complete") else getattr(llm, "llm", None)
        if provider is None or not result or not (result[0] or "").strip():
            return False
        new = await polish_narration(provider, result[0], on_start=on_start)
        if new is None:
            return False
        from app.services.turn_context import _remap_marks_after_rewrite
        old = result[0]
        result[0] = new
        _remap_marks_after_rewrite(result, old, event_order)
        logger.info("去口癖编辑器已改写落库旁白：%d → %d 处口癖", tic_score(old), tic_score(new))
        return True
    except Exception:
        logger.exception("去口癖编辑器异常，保留原文")
        return False
