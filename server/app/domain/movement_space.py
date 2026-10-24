"""基于只读空间快照解析目的地；连通候选集由应用层提供。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.domain.movement_language import SENTENCE_SPLIT, has_any, exit_words


@dataclass(frozen=True)
class ExitSnapshot:
    target: str
    names: tuple[str, ...]
    description: str = ""


@dataclass(frozen=True)
class SceneSnapshot:
    id: str
    terms: frozenset[str]
    description: str
    exits: tuple[ExitSnapshot, ...]


@dataclass(frozen=True)
class SpaceSnapshot:
    scenes: tuple[SceneSnapshot, ...]
    location_ids: tuple[str, ...]
    candidates: tuple[str, ...]

    def scene(self, scene_id: str | None) -> SceneSnapshot | None:
        return next((s for s in self.scenes if s.id == str(scene_id or "")), None)

    def terms(self, scene_id: str) -> frozenset[str]:
        scene = self.scene(scene_id)
        return scene.terms if scene else frozenset()


#: 虚词与人称：含这些字的二字片段没有区分力（「的拱」「我从」）。
_FUNCTION_CHARS = set("的了着把在是有就也都我你他她它们从往向朝这那个一和与")


def _bigrams(text: str, skip: set[str]) -> set[str]:
    """玩家原话里有区分力的二字片段（「北侧」「石板」），用来在多个同类出口之间按方位/外观区分。

    剔除与出口名词共字的片段（「侧拱」「门钻」）和含虚词的片段。
    """
    clean = re.sub(r"[^一-鿿0-9A-Za-z]", "", text or "")
    banned = set("".join(skip)) | _FUNCTION_CHARS
    grams = {clean[i:i + 2] for i in range(len(clean) - 1)}
    return {g for g in grams if not (set(g) & banned)}


@dataclass
class ExitUnit:
    """场景描述里的一个出口分句（或模组显式声明的一条出口）。"""

    text: str
    classes: set[str]
    targets: list[str]
    names: list[str] = field(default_factory=list)   # 显式出口的名字与别名（字面命中优先）


def _exit_units(space: SpaceSnapshot, origin: str | None, candidates: list[str]) -> list[ExitUnit]:
    scene = space.scene(origin)
    units: list[ExitUnit] = []
    loc_ids = set(space.location_ids)
    for ex in scene.exits if scene else ():
        to = ex.target
        if to not in loc_ids:
            continue
        names = list(ex.names)
        text = "、".join(n for n in names if n) + ex.description
        units.append(ExitUnit(
            text, {c for _w, c in exit_words(text)}, [to], [n for n in names if len(n) >= 2],
        ))
    if units:
        return units
    description = scene.description if scene else ""
    for sentence in SENTENCE_SPLIT.split(description):
        mentioned = [sid for sid in candidates if has_any(sentence, space.terms(sid))]
        pieces = [sentence] if len(mentioned) <= 1 else re.split(r"[，,、]", sentence)
        for piece in pieces:
            classes = {c for _w, c in exit_words(piece)}
            if not classes:
                continue
            targets = [sid for sid in candidates if has_any(piece, space.terms(sid))]
            units.append(ExitUnit(piece.strip(), classes, targets))
    return units


@dataclass
class Destination:
    status: str          # resolved / ambiguous / unresolved
    target: str | None = None
    candidates: list[str] = field(default_factory=list)
    clues: list[str] = field(default_factory=list)   # 对得上的出口分句（只给 KP 看）


def resolve_destination(
    space: SpaceSnapshot, origin: str | None, text: str, planner_exit: str = "",
) -> Destination:
    """把「穿过拱门」「从那个缺口钻进去」解析成具体场景。

    依据依次是：原话直接点名的地点 → 出口名词按描述分句（或显式 exits）归属到相邻场景 →
    原话里的方位/外观片段在多个同类出口间区分 → 相邻场景只有一个时，去向本身就是唯一的。
    规划器给的 target 不参与：它不能单独构成证据，多个出口对得上时也不替玩家挑——那要 KP 去问。
    规划器转述的出口说法（planner_exit）只在原话没提出口时补位，或帮着按方位区分。
    """
    candidates = list(space.candidates)
    named = [sid for sid in space.location_ids if sid != origin and has_any(text, space.terms(sid))]
    if len(named) == 1:
        return Destination("resolved", named[0], named)
    if len(named) > 1:
        return Destination("ambiguous", None, named)

    words = exit_words(text) or exit_words(planner_exit)
    if not candidates:
        return Destination("unresolved")
    if not words:
        # 没提出口（「我跟着钻进去」「我也进去」）：相邻只有一处时去向唯一，否则须澄清。
        if len(candidates) == 1:
            return Destination("resolved", candidates[0], candidates)
        return Destination("ambiguous", None, candidates)

    all_units = _exit_units(space, origin, candidates)
    literal = {sid for u in all_units for sid in u.targets if any(n in text for n in u.names)}
    if len(literal) == 1:
        return Destination("resolved", literal.pop(), list(candidates))
    classes = {cls for _w, cls in words}
    units = [u for u in all_units if u.classes & classes]
    attributed: dict[str, list[str]] = {}
    unattributed = False
    for unit in units:
        if not unit.targets:
            unattributed = True
        for sid in unit.targets:
            attributed.setdefault(sid, []).append(unit.text)
    found = list(attributed)
    if unattributed:
        # 描述里有这类出口却没写通往哪：没被任何出口分句认领的相邻场景若只剩一处，就是它。
        claimed = {sid for u in all_units for sid in u.targets}
        rest = [sid for sid in candidates if sid not in claimed]
        if len(rest) == 1 and rest[0] not in found:
            found.append(rest[0])
            attributed[rest[0]] = [u.text for u in units if not u.targets]
    if not found and len(candidates) == 1:
        found = list(candidates)
        attributed[candidates[0]] = []
    if len(found) > 1:
        skip = {w for w, _c in words}
        for source in (text, planner_exit):
            grams = _bigrams(source, skip)
            narrowed = [sid for sid in found if any(g in "".join(attributed[sid]) for g in grams)]
            if len(narrowed) == 1:
                found = narrowed
                break
    clues = [c for sid in found for c in attributed.get(sid, [])]
    if len(found) == 1:
        return Destination("resolved", found[0], found, clues)
    if not found:
        return Destination("unresolved", None, [], clues)
    return Destination("ambiguous", None, found, clues)


