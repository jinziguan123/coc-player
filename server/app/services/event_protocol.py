"""房间事件与 SSE chunk 的传输协议。"""

from __future__ import annotations

import re

from app.services.room_events import RoomEvent


OOC_RE = re.compile(r"（[^（）]*）|\([^()]*\)")
QUOTE_RE = re.compile(r'[“"「『]([^”"」』]*)[”"」』]')


def split_speech_action(text: str) -> list[tuple[str, str]]:
    """按引号约定把玩家输入拆成有序的行动与台词。"""
    segments: list[tuple[str, str]] = []
    last = 0
    for match in QUOTE_RE.finditer(text or ""):
        before = (text[last : match.start()] or "").strip(" \t\n，,。.、")
        if before:
            segments.append(("action", before))
        inner = (match.group(1) or "").strip()
        if inner:
            segments.append(("dialogue", inner))
        last = match.end()
    tail = (text[last:] if text else "").strip(" \t\n，,。.、")
    if tail:
        segments.append(("action", tail))
    return segments


def split_ooc(text: str) -> tuple[str, str]:
    """拆出正式行动与 OOC 内容，返回 ``(in_character, ooc)``。"""
    ooc_parts = OOC_RE.findall(text or "")
    in_character = OOC_RE.sub("", text or "").strip()
    ooc = " ".join(part[1:-1].strip() for part in ooc_parts if len(part) >= 2).strip()
    return in_character, ooc


def make_chunk(
    chunk_type: str,
    content: str = "",
    actor_name: str | None = None,
    metadata: dict | None = None,
    event_id: str | None = None,
    actor_id: str | None = None,
) -> RoomEvent:
    """构造一条房间事件。

    只负责「造」，不负责「编码」——编码是传输层的事（见 ``room_hub.encode_sse``）。
    未登记的 ``chunk_type`` 会在这里被 Pydantic 挡下，而不是悄悄发到前端后无人处理。
    空 metadata 归一成 None，保持与收口前一致的线上负载（不下发空对象）。
    """
    return RoomEvent(
        type=chunk_type,  # type: ignore[arg-type]  # 由 Pydantic 按 Literal 校验
        content=content,
        actor_name=actor_name or None,
        metadata=metadata or None,
        id=event_id or None,
        actor_id=actor_id or None,
    )


def event_to_chunk(event) -> RoomEvent:
    """把持久 EventLog 转成 `/live` 重放用的事件。"""
    type_map = {
        "dialogue": "dialogue",
        "action": "action",
        "dice": "dice",
        "narration": "narration_full",
        "system": "system",
        "ooc": "ooc",
    }
    return make_chunk(
        type_map.get(event.event_type, event.event_type),
        event.content,
        actor_name=event.actor_name or None,
        metadata=event.metadata_ or None,
        event_id=event.id,
        actor_id=event.actor_id,
    )
