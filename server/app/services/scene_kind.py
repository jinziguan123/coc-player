"""场景类型（kind）与剧情事件闸门：纯函数，不碰数据库、不碰 ai/。

模组的 ``scenes`` 里混着三种东西，只有一种是「地方」：

- ``location``（缺省）：真实存在、玩家能前往的地点——唯一上地图、可前往、计入地点数的一类；
- ``chapter``：纯叙事章节（「委托与准备」「尾声」）；
- ``event``：按条件触发的剧情段落（「可怖之物苏醒」）。它带 ``when``（触发条件，照抄
  原文）、``npcs``（涉及的 NPC id）、``set_flags``（触发后置的标志）。

**事件闸门**：事件的 ``set_flags`` 任一已激活即视为已触发；未触发事件绑定的 NPC 处于
「蛰伏」——幕后推演不让它行动，KP 上下文明确标注它在等什么条件。这是确定性闸门，
不靠模型自觉。
"""

from __future__ import annotations

LOCATION = "location"
CHAPTER = "chapter"
EVENT = "event"
KINDS = (LOCATION, CHAPTER, EVENT)


def scene_kind(scene: dict | None) -> str:
    """场景类型；缺省 / 未知值一律视为 location（与入库规整同一口径）。"""
    kind = str((scene or {}).get("kind") or "").strip().lower()
    return kind if kind in KINDS else LOCATION


def is_location(scene: dict | None) -> bool:
    """是否是玩家能前往的地点——地图、导航、前往、地点计数只认这一类。"""
    return isinstance(scene, dict) and scene_kind(scene) == LOCATION


def location_scenes(scenes: list | None) -> list[dict]:
    return [s for s in scenes or [] if is_location(s)]


def event_scenes(scenes: list | None) -> list[dict]:
    return [s for s in scenes or [] if isinstance(s, dict) and scene_kind(s) == EVENT]


def _str_list(v) -> list[str]:
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, (list, tuple)):
        return []
    out: list[str] = []
    for x in v:
        s = str(x or "").strip()
        if s and s not in out:
            out.append(s)
    return out


def event_flags(event: dict) -> list[str]:
    """事件触发后置的标志；没写的按 ``<场景id>_triggered`` 兜底（规整时已写回）。"""
    flags = _str_list(event.get("set_flags"))
    if not flags and event.get("id"):
        flags = [f"{event['id']}_triggered"]
    return flags


def event_triggered(event: dict, flags: set[str]) -> bool:
    return any(f in flags for f in event_flags(event))


def normalize_scene_kinds(scenes: list | None, npc_ids: set[str] | None = None) -> list:
    """入库规整（原地改）：kind 归一到三类之一；事件补齐 when / npcs / set_flags。

    ``npc_ids`` 给出时，事件的 npcs 只保留模组里真实存在的 NPC id（丢幻觉）。
    """
    for s in scenes or []:
        if not isinstance(s, dict):
            continue
        s["kind"] = scene_kind(s)
        if s["kind"] != EVENT:
            continue
        s["when"] = str(s.get("when") or "").strip()
        npcs = _str_list(s.get("npcs"))
        if npc_ids is not None:
            npcs = [n for n in npcs if n in npc_ids]
        s["npcs"] = npcs
        s["set_flags"] = event_flags(s)
    return scenes or []


def pending_events(scenes: list | None, flags: set[str]) -> list[dict]:
    """尚未触发的事件（按模组顺序）。"""
    return [e for e in event_scenes(scenes) if not event_triggered(e, flags)]


def dormant_npcs(scenes: list | None, flags: set[str]) -> dict[str, list[dict]]:
    """蛰伏中的 NPC：npc_id → 它所等待的未触发事件列表。"""
    out: dict[str, list[dict]] = {}
    for e in pending_events(scenes, flags):
        for nid in _str_list(e.get("npcs")):
            out.setdefault(nid, []).append(e)
    return out


def event_title(event: dict) -> str:
    return str(event.get("title") or event.get("name") or event.get("id") or "")
