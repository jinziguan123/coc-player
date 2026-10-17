"""世界对象的最小注册表：把自由文本 target 收敛到稳定对象 id。

不建完整对象模型，只解决「队友说门、NPC 说地下室的门、系统要判同一对象」的问题：
- 模组场景 → scene:<id>
- 模组/临场 NPC → npc:<id>
- 其他对象 → world_state.world_objects 里的 object:<id>
- 都没有 → 规范化自由文本（保持旧行为）
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

_WORLD_OBJECTS_KEY = "world_objects"


@dataclass(frozen=True)
class ObjectRef:
    id: str
    name: str
    kind: str


def _norm(value) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _matches(raw: str, candidates) -> bool:
    key = _norm(raw)
    if not key:
        return False
    return any(key == _norm(c) or key in _norm(c) or _norm(c) in key for c in candidates if c)


def resolve(module, session, raw: str) -> str:
    """返回稳定 target key；匹配不到时返回规范化原文。"""
    key = _norm(raw)
    if not key:
        return ""
    for scene in (getattr(module, "scenes", None) or []):
        sid = str(scene.get("id") or "").strip()
        if sid and _matches(key, (sid, scene.get("name"), scene.get("title"))):
            return f"scene:{sid}"
    for npc in (getattr(module, "npcs", None) or []):
        nid = str(npc.get("id") or "").strip()
        if nid and _matches(key, (nid, npc.get("name"))):
            return f"npc:{nid}"
    ws_objects = (getattr(session, "world_state", None) or {}).get(_WORLD_OBJECTS_KEY) or {}
    for oid, entry in ws_objects.items():
        if isinstance(entry, dict) and _matches(
            key, (oid, entry.get("name"), *(entry.get("aliases") or [])),
        ):
            return str(oid)
    return key


def register(
    world_state: dict, *, object_id: str, name: str, kind: str = "object",
    scene_id: str = "", aliases: tuple[str, ...] = (),
) -> dict:
    ws = deepcopy(dict(world_state or {}))
    table = dict(ws.get(_WORLD_OBJECTS_KEY) or {})
    table[object_id] = {
        "name": name,
        "kind": kind,
        "scene_id": scene_id,
        "aliases": list(aliases),
    }
    ws[_WORLD_OBJECTS_KEY] = table
    return ws
