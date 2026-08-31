from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from collections.abc import AsyncIterator

from app.services.room_events import RoomEvent

logger = logging.getLogger(__name__)

# 单个订阅者的待发队列上限。正常读取速度下永远到不了这个量级（叙述流按 token
# 推送，但客户端同步消费）；真触顶只说明该连接已经卡死或对端消失，此时直接终止
# 它，让前端走既有的自动重连 + 从 DB 全量对齐，而不是让内存无限涨。
MAX_PENDING_CHUNKS = 2048

# SSE 心跳间隔（秒）。长时间无事件的连接会被 NAT 表项回收、被反代/隧道按空闲超时
# 掐断，且断开往往不通知任一端。定期发一行 SSE 注释行保活；注释行不是 `data:`
# 开头，前端 SSE 解析器天然忽略，不需要配套改动。
HEARTBEAT_SECONDS = 15.0


def encode_sse(event: RoomEvent) -> str:
    """把一条房间事件编码成 SSE data 行。

    编码是传输层的职责，业务代码只造 ``RoomEvent``、不碰 ``data:`` 前缀——
    将来换成 WebSocket 只需要换掉这里和 ``stream_room``。
    """
    return f"data: {json.dumps(event.as_wire(), ensure_ascii=False)}\n\n"


class RoomHub:
    """房间级常驻广播通道（阶段 2 实时联机的唯一输出出口）。

    与 ``GenerationManager`` 不同：订阅者集合**不随单次生成结束而清空**，
    而是与成员的 ``/live`` SSE 连接同寿命。生成产物、玩家行动、检定、OOC、
    入座/在场等一切要让全房间看到的事件，都经 ``broadcast`` 下发。

    ``_inflight`` 缓存「当前生成」已广播的 chunk，供生成期间中途接入的订阅者
    立即重放，看到正在流式的叙述；生成结束即清空。离散持久事件的可靠补全
    由 ``/live`` 连接时从 ``event_logs`` 重放负责，不依赖本 buffer。
    """

    def __init__(self) -> None:
        self._subs: dict[str, list[asyncio.Queue]] = defaultdict(list)
        self._inflight: dict[str, list[str]] = {}
        # 在线状态：room → {token: 该 token 的活跃 /live 连接数}
        self._presence: dict[str, dict[str, int]] = defaultdict(dict)
        # token → 它的所有活跃队列（跨房间）。吊销一个客户端时要能立刻掐掉它的 SSE，
        # 而 _subs 只按房间索引，拿着 token 找不到队列。
        self._by_token: dict[str, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, room_id: str, token: str | None = None) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=MAX_PENDING_CHUNKS)
        # 生成中途接入：先把当前生成已广播的 chunk 重放给新订阅者。
        # buffer 超过队列容量时只保留最后一段——前面的内容客户端随后会从 DB 拉到。
        inflight = self._inflight.get(room_id, [])
        for chunk in inflight[-MAX_PENDING_CHUNKS:]:
            q.put_nowait(chunk)
        self._subs[room_id].append(q)
        if token:
            self._presence[room_id][token] = self._presence[room_id].get(token, 0) + 1
            self._by_token[token].add(q)
        return q

    def unsubscribe(
        self, room_id: str, q: asyncio.Queue, token: str | None = None
    ) -> None:
        subs = self._subs.get(room_id, [])
        if q in subs:
            subs.remove(q)
        if not subs:
            self._subs.pop(room_id, None)
        if token:
            p = self._presence.get(room_id)
            if p and token in p:
                p[token] -= 1
                if p[token] <= 0:
                    del p[token]
                if not p:
                    self._presence.pop(room_id, None)
            held = self._by_token.get(token)
            if held is not None:
                held.discard(q)
                if not held:
                    self._by_token.pop(token, None)

    def online_tokens(self, room_id: str) -> set[str]:
        return set(self._presence.get(room_id, {}).keys())

    def broadcast(self, room_id: str, chunk: RoomEvent) -> None:
        if not isinstance(chunk, RoomEvent):
            # 曾经踩过：combat_service / chase_service 各自拼 SSE 字符串绕开 make_chunk，
            # 事件类型既不在注册表也不受校验，直到 stream_room 编码时才炸——而且只炸在
            # 某个订阅者的连接上，调用方毫无感知。在入口挡住，让错误落在生产者身上。
            raise TypeError(
                f"broadcast 只接受 RoomEvent，收到 {type(chunk).__name__}；"
                "请用 event_protocol.make_chunk 构造事件，不要自己拼 SSE 字符串"
            )
        buf = self._inflight.get(room_id)
        if buf is not None:
            buf.append(chunk)
        for q in self._subs.get(room_id, []):
            try:
                q.put_nowait(chunk)
            except asyncio.QueueFull:
                self._drop_stalled(room_id, q)

    def _drop_stalled(self, room_id: str, q: asyncio.Queue) -> None:
        """队列积压触顶：清空并投 ``None`` 终止该连接。

        ``stream_room`` 收到 ``None`` 即结束生成器，FastAPI 关闭这条 SSE，前端的
        重连循环会 ``resyncHistory()`` 从 DB 全量对齐——比继续往一个读不动的队列里
        塞、或者静默丢事件让该客户端停在错误状态要好。退订由 ``stream_room`` 的
        ``finally`` 负责，这里不动订阅表。
        """
        while not q.empty():
            q.get_nowait()
        q.put_nowait(None)
        logger.warning("房间 %s 有订阅者积压超过 %d 条，已断开令其重连", room_id, MAX_PENDING_CHUNKS)

    def disconnect_token(self, token: str) -> int:
        """掐掉某个客户端的所有 /live 连接，返回掐掉的条数。

        吊销之后它的下一个 HTTP 请求会吃 403——但 /live 是条**已经建好的**长连接，
        不发新请求也照收房间事件。不掐它，「吊销」就只是个名义动作：被踢的人还能
        接着看这一桌在演什么。

        投 ``None`` 的手法与 ``_drop_stalled`` 同源，``stream_room`` 收到即结束生成器。
        差别在于那边是让对方重连，这边对方重连也进不来了。
        """
        queues = list(self._by_token.get(token, ()))
        for q in queues:
            while not q.empty():
                q.get_nowait()
            q.put_nowait(None)
        if queues:
            logger.info("吊销客户端 %s…，掐断 %d 条实时连接", token[:8], len(queues))
        return len(queues)

    def begin_generation(self, room_id: str) -> None:
        self._inflight[room_id] = []

    def end_generation(self, room_id: str) -> None:
        self._inflight.pop(room_id, None)

    def member_count(self, room_id: str) -> int:
        return len(self._subs.get(room_id, []))


room_hub = RoomHub()


async def stream_room(
    room_id: str,
    q: asyncio.Queue,
    token: str | None = None,
    heartbeat: float = HEARTBEAT_SECONDS,
) -> AsyncIterator[str]:
    """把房间订阅队列转成 SSE 文本流；空闲时发心跳，断开时自动退订并广播在线变更。"""
    try:
        while True:
            try:
                chunk = await asyncio.wait_for(q.get(), timeout=heartbeat)
            except asyncio.TimeoutError:
                yield ": ping\n\n"  # SSE 注释行：只为保活，前端解析器会忽略
                continue
            if chunk is None:
                break
            yield encode_sse(chunk)
    finally:
        room_hub.unsubscribe(room_id, q, token)
        room_hub.broadcast(room_id, RoomEvent(type="presence"))
