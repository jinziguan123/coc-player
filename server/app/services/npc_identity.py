"""NPC 的「对外称呼」：玩家还没认出来的东西，机制界面不能替 KP 把名字说出来。

模组里的 NPC 名同时充当两种角色——KP 侧的唯一标识，和玩家看到的显示名。
对普通 NPC 无所谓，对怪物就是剧透：叙事里 KP 好好地写着「一团比夜色更浓的黑…
没有脸，没有眼」，对抗检定卡却在旁边印出「田间潜随者（莎布·尼古拉丝化身）」，
玩家一眼就知道自己在跟哪尊旧日支配者的化身打交道，悬念当场作废。

**判据是 KP 自己的叙事**：玩家正是从叙事里认识这些东西的。KP 在公开叙事/对白里
写过这个名字 → 玩家已经知道 → 机制界面照常显示；没写过 → 显示中性称呼。
不需要额外标注，也不给 KP 加负担，而且自动跟上剧情：等玩家读到典籍、
NPC 说破身份、KP 在叙事里直呼其名的那一刻，机制界面自然跟着改口。

模组里的名字普遍写成「外号（神话身份）」这种形式（田间潜随者（莎布·尼古拉丝化身）、
呼子（蠕虫行者）），括号里那层本就是给 KP 看的，所以揭示分三级：
神话身份出现过 → 全名；只有外号出现过 → 只给外号；都没出现 → 中性称呼。
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from sqlalchemy.orm import Session

from app.models.module import Module
from app.services import session_service

#: 玩家还认不出这东西时，机制界面上的称呼。
#: 模组可以给单个 NPC 写 ``unknown_as`` 覆盖成更贴切的（如「树影里的东西」）。
UNKNOWN_LABEL = "不明存在"

#: 只有这两类事件算「玩家从中认识了这个名字」：KP 写的公开叙事与对白。
#: 刻意不含 dice/system/action——机制事件的名字正是本模块要遮的东西，
#: 拿它当揭示依据会自我实现：卡上印一次，就永远算「玩家已知」了。
_PROSE_EVENT_TYPES = ("narration", "dialogue")

_PAREN = re.compile(r"[（(]([^（()）]+)[)）]\s*$")


def split_name(name: str) -> tuple[str, str]:
    """把「外号（神话身份）」拆成 (外号, 神话身份)；没有括号时后者为空。"""
    text = (name or "").strip()
    m = _PAREN.search(text)
    if not m:
        return text, ""
    return text[: m.start()].strip(), m.group(1).strip()


class NameMasker:
    """按「这局玩家看过的叙事」把 NPC 名换成对外称呼。

    一局里要遮的名字往往不止一个（战斗一轮就要过一遍先攻表），所以做成
    一次构建、多次调用：叙事正文只拼一次。
    """

    def __init__(self, prose: str, npcs: Iterable[dict] | None = None):
        self._prose = prose or ""
        self._npcs: dict[str, dict] = {}
        for npc in npcs or []:
            name = str(npc.get("name") or "").strip()
            if name:
                self._npcs[name] = npc

    def _resolve(self, name: str, npc: dict, prose: str) -> str:
        base, epithet = split_name(name)
        # 知道了神话身份，再遮外号没有意义——全名照给
        if epithet and epithet in prose:
            return name
        if base and base in prose:
            return base
        return str(npc.get("unknown_as") or "").strip() or UNKNOWN_LABEL

    def __call__(self, name: str | None, extra_prose: str = "") -> str:
        """取对外称呼。不是本模组 NPC 的名字（玩家角色、临场 NPC）原样返回。

        ``extra_prose`` 给「这一条还没落库的叙事/台词」用：NPC 自报家门那一刻
        （「我叫香澄澪」）就该同时改口，否则气泡会是「不明存在：我叫香澄澪」。
        """
        text = (name or "").strip()
        npc = self._npcs.get(text)
        if npc is None:
            return text
        prose = f"{self._prose}\n{extra_prose}" if extra_prose else self._prose
        return self._resolve(text, npc, prose)

    def reveals(self, name: str | None) -> bool:
        """这个名字现在是不是照实显示（供调用方判断要不要额外藏细节）。"""
        text = (name or "").strip()
        return self(text) == text


def _player_visible_prose(db: Session, session_id: str) -> str:
    """本局玩家看得到的 KP 叙事与对白正文。

    「仅 KP 可见」的事件（幕后推演等）不算数——玩家没看过的东西不能算他知道。
    """
    parts: list[str] = []
    for ev in session_service.get_session_events(db, session_id):
        if ev.event_type not in _PROSE_EVENT_TYPES:
            continue
        if session_service.is_kp_only_event(ev):
            continue
        parts.append(ev.content or "")
    return "\n".join(parts)


def build_masker(db: Session, session_id: str, module: Module | None) -> NameMasker:
    """按当前进度构建一个对外称呼表。"""
    npcs = list(getattr(module, "npcs", None) or []) if module is not None else []
    npcs = [n for n in npcs if isinstance(n, dict)]
    if not npcs:
        return NameMasker("", [])
    return NameMasker(_player_visible_prose(db, session_id), npcs)


def public_name(
    db: Session, session_id: str, module: Module | None, name: str | None,
) -> str:
    """单个名字的对外称呼。要遮多个名字时用 :func:`build_masker`，别重复扫事件。"""
    return build_masker(db, session_id, module)(name)
