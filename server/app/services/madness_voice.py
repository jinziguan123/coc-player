"""疯狂发作时的玩家发言改写（可选开关）。

玩家在设置里开启后，处于疯狂状态的玩家**台词**会经快模型改写成符合症状的口吻：
保留全部意图、信息与目标，只调整措辞与视角；改写失败或结果异常时一律回退原文，
绝不吞掉玩家输入。
"""

from __future__ import annotations

import asyncio
import logging

from app.ai.llm_factory import get_fast_llm

logger = logging.getLogger(__name__)

#: 会触发改写的身心状态（永久/不定期也改写；临时疯狂最常见）。
INSANE_STATUSES = frozenset(
    {"temporary_insanity", "indefinite_insanity", "permanent_insanity"},
)

_SYSTEM_PROMPT = """你是跑团里的文字编辑。玩家角色正处在「{label}」的疯狂发作中：{manifest}。
请把玩家下面这段**说出口的台词**改写成受该症状影响的口吻：
- 保留全部意图、信息、人名、地名、目标与事实，绝不新增剧情、不替玩家做决定；
- 只调整措辞、语气、停顿与表达方式，让话听起来像这个人此刻的疯癫状态；
- 不要加旁白、不要加引号、不要解释，只输出改写后的台词正文一句。"""


def is_insane(character) -> bool:
    return bool(character and (getattr(character, "status", "") or "") in INSANE_STATUSES)


def _madness_brief(character) -> tuple[str, str]:
    mad = (getattr(character, "system_data", None) or {}).get("madness") or {}
    return str(mad.get("label") or "").strip(), str(mad.get("manifest") or "").strip()


async def rewrite_dialogue(
    text: str, character, *, timeout: float = 12.0,
) -> str:
    """把一句台词语气改写成疯狂口吻；不可用/失败/异常时返回原文。"""
    text = (text or "").strip()
    if not text or not is_insane(character):
        return text
    label, manifest = _madness_brief(character)
    if not label:
        return text
    prompt = _SYSTEM_PROMPT.format(label=label, manifest=manifest or "言行失控")
    try:
        out = await asyncio.wait_for(
            get_fast_llm().complete(
                [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": text},
                ],
                temperature=0.7,
                max_tokens=400,
            ),
            timeout=timeout,
        )
        out = (out or "").strip()
        # 安全阀：空结果、或模型自作主张加引号/换行/解释，一律回退原文
        if not out or "\n" in out or any(q in out for q in ("「", "」", "『", "』", '"')):
            return text
        return out
    except Exception:  # noqa: BLE001 — 改写是增强，失败回退原文
        logger.exception("疯狂台词改写失败，回退原文：char=%s", getattr(character, "id", ""))
        return text
