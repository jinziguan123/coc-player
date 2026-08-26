"""房间事件的类型注册表与线上格式：全应用唯一真源。

在此之前，事件类型是散落在 20 多个文件里的裸字符串，前端靠一长串 `if` 分支认它们，
两边没有任何机制保证对得上——后端加一种事件而前端忘了处理，不会有任何报错。

本模块把类型收成一个 ``Literal``，并给每种类型标注**生命周期分类**。分类不是装饰，
它决定这类事件怎么持久化、怎么重放、怎么去重——三者规则完全不同，此前混在一个扁平
命名空间里，正是重连逻辑要写一堆特判的根因：

- ``stream``：流控与流式片段。不进 event_logs、不重放、没有 id，最后一条为准。
- ``log``：叙事日志。进 event_logs，带 id 与 seq，客户端按 id 去重，断线后从 DB 补。
- ``sync``：状态失效通知。真值在业务表里，事件本身只说明「某状态变了」。

``RoomEvent`` 通过 ``GET /api/sessions/{id}/live/_schema`` 进入 OpenAPI，前端由既有的
``pnpm api:generate`` 拿到判别用的字符串字面量联合——前端那份 ``Record<RoomEventType, …>``
因此必须列全所有类型，漏一个就编译不过。这是把契约治理落成 CI 门禁的地方。

**线上字段名保持不变**（``type`` 仍是 ``dialogue`` 而不是 ``log.dialogue``）。加前缀会
让分类在线上自描述，但要同时改 200 多处调用与前端全部分支，且破坏所有旧客户端；
先把单一真源立起来，改名留到 P-Msg-3 —— 那时全部类型只在这一个文件里列着，改名是
局部操作。
"""

from __future__ import annotations

from typing import Literal, get_args

from pydantic import BaseModel

# 协议版本：线上格式或类型集合发生不兼容变更时 +1。房主与客人版本不一致时，
# 客人连接会明确提示升级，而不是以奇怪的方式半坏。
PROTOCOL_VERSION = 1

Category = Literal["stream", "log", "sync"]

RoomEventType = Literal[
    # --- stream：流控与流式片段 ---
    "generating",
    "done",
    "ready",
    "typing",
    "presence",
    "housekeeping",
    "narration",  # 流式叙述片段；完整版落库后另以 narration_full 重放
    # --- log：进 event_logs 的叙事日志 ---
    "dialogue",
    "action",
    "dice",
    "narration_full",
    "npc_dialogue",
    "system",
    "ooc",
    "check_request",
    # --- sync：状态失效通知 ---
    "lobby",
    "seat",
    "started",
    "status",
    "end_vote",
    "turn_state",
    "character_update",
    "inventory_update",
    "kp_turn_ready",
    "kp_roll_ready",
    "kp_action",
    "kp_request",
    "event_update",
    "event_delete",
    "event_patch",
    "combat_start",
    "combat_state",
    "combat_reaction_prompt",
    "combat_end",
    "chase_start",
    "chase_state",
    "chase_end",
    "map_update",
    "rule_options",
    "luck_offer",
]

CATEGORY: dict[str, Category] = {
    "generating": "stream",
    "done": "stream",
    "ready": "stream",
    "typing": "stream",
    "presence": "stream",
    "housekeeping": "stream",
    "narration": "stream",
    "dialogue": "log",
    "action": "log",
    "dice": "log",
    "narration_full": "log",
    "npc_dialogue": "log",
    "system": "log",
    "ooc": "log",
    "check_request": "log",
    "lobby": "sync",
    "seat": "sync",
    "started": "sync",
    "status": "sync",
    "end_vote": "sync",
    "turn_state": "sync",
    "character_update": "sync",
    "inventory_update": "sync",
    "kp_turn_ready": "sync",
    "kp_roll_ready": "sync",
    "kp_action": "sync",
    "kp_request": "sync",
    "event_update": "sync",
    "event_delete": "sync",
    "event_patch": "sync",
    "combat_start": "sync",
    "combat_state": "sync",
    "combat_reaction_prompt": "sync",
    "combat_end": "sync",
    "chase_start": "sync",
    "chase_state": "sync",
    "chase_end": "sync",
    "map_update": "sync",
    "rule_options": "sync",
    "luck_offer": "log",
}

ALL_TYPES: tuple[str, ...] = get_args(RoomEventType)

# 两份清单必须严丝合缝：漏标分类的类型会在 CATEGORY[...] 处 KeyError，
# 与其等到运行时某条事件恰好走到那里，不如在导入期就炸。
assert set(ALL_TYPES) == set(CATEGORY), (
    f"事件类型与分类表不一致：仅在类型里 {set(ALL_TYPES) - set(CATEGORY)}，"
    f"仅在分类表里 {set(CATEGORY) - set(ALL_TYPES)}"
)


class RoomEvent(BaseModel):
    """一条房间事件的线上格式。

    字段顺序即 JSON 键顺序，与收口前 ``make_chunk`` 手搓的顺序保持一致。
    值为 ``None`` 的字段不下发（见 ``as_wire``），所以线上负载没有变化。
    """

    type: RoomEventType
    content: str = ""
    actor_name: str | None = None
    metadata: dict | None = None
    id: str | None = None
    actor_id: str | None = None

    def as_wire(self) -> dict:
        return self.model_dump(exclude_none=True)

    @property
    def category(self) -> Category:
        return CATEGORY[self.type]
