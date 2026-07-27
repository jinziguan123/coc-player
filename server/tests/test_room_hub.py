"""RoomHub 房间级常驻广播的单元测试。"""

import asyncio

from app.services.room_events import RoomEvent
from app.services.room_hub import (
    MAX_PENDING_CHUNKS,
    RoomHub,
    encode_sse,
    stream_room,
)


def test_broadcast_reaches_all_subscribers():
    hub = RoomHub()
    a = hub.subscribe("r1")
    b = hub.subscribe("r1")
    ev = RoomEvent(type="system", content="x")
    hub.broadcast("r1", ev)
    assert a.get_nowait() is ev
    assert b.get_nowait() is ev
    assert hub.member_count("r1") == 2


def test_broadcast_isolated_per_room():
    hub = RoomHub()
    a = hub.subscribe("r1")
    hub.subscribe("r2")
    hub.broadcast("r2", RoomEvent(type="system", content="y"))
    assert a.empty()  # r1 订阅者收不到 r2 的广播


def test_inflight_replay_for_midstream_join():
    hub = RoomHub()
    hub.begin_generation("r1")
    c1, c2 = RoomEvent(type="system", content="1"), RoomEvent(type="system", content="2")
    hub.broadcast("r1", c1)
    hub.broadcast("r1", c2)
    # 生成中途接入：应立即重放已广播的 chunk
    late = hub.subscribe("r1")
    assert late.get_nowait() is c1
    assert late.get_nowait() is c2
    # 结束生成后清空 buffer，新订阅者不再重放
    hub.end_generation("r1")
    later = hub.subscribe("r1")
    assert later.empty()


def test_generation_prelude_is_buffered():
    """start 的 prelude（玩家事件 + generating）在 begin_generation 之后广播，进入 buffer，
    断线重连可重放——避免「点了发送但自己消息没显示、只剩思考中」的吞消息问题。"""
    from app.services.generation_manager import GenerationManager
    from app.services.room_hub import room_hub

    async def _noop():
        await asyncio.sleep(0.01)

    async def run():
        gm = GenerationManager()
        prelude = [RoomEvent(type="action", content="player-evt"), RoomEvent(type="generating")]
        task = gm.start("r_prelude", _noop(), prelude=prelude)
        late = room_hub.subscribe("r_prelude")  # 中途接入：应重放 prelude
        got = [late.get_nowait(), late.get_nowait()]
        await task
        return got

    assert [e.type for e in asyncio.run(run())] == ["action", "generating"]


def test_unsubscribe_removes():
    hub = RoomHub()
    a = hub.subscribe("r1")
    hub.unsubscribe("r1", a)
    assert hub.member_count("r1") == 0
    hub.broadcast("r1", RoomEvent(type="system", content="z"))  # 不应抛错
    assert a.empty()


def test_stream_room_yields_until_none():
    hub = RoomHub()
    q = hub.subscribe("r1")
    a, b = RoomEvent(type="typing"), RoomEvent(type="presence")
    q.put_nowait(a)
    q.put_nowait(b)
    q.put_nowait(None)

    # stream_room 使用模块级单例 room_hub 退订，这里只验证产出序列
    async def collect():
        out = []
        async for c in stream_room("r1", q):
            out.append(c)
        return out

    # 传输层负责编码：进队列的是事件对象，出去的是 SSE 文本
    assert asyncio.run(collect()) == [encode_sse(a), encode_sse(b)]


def test_stream_room_emits_heartbeat_when_idle():
    """空闲连接定期发 SSE 注释行保活，否则会被 NAT/反代按空闲超时静默掐断。"""
    hub = RoomHub()
    q = hub.subscribe("r1")

    async def collect():
        out = []
        async for c in stream_room("r1", q, heartbeat=0.01):
            out.append(c)
            if len(out) == 2:
                break
        return out

    assert asyncio.run(collect()) == [": ping\n\n", ": ping\n\n"]


def test_heartbeat_does_not_swallow_events():
    """心跳靠 wait_for 超时实现，超时取消 get() 不能把随后到达的事件弄丢。"""
    hub = RoomHub()
    q = hub.subscribe("r1")

    async def collect():
        async def feed():
            await asyncio.sleep(0.05)  # 先空转出若干次心跳
            hub.broadcast("r1", RoomEvent(type="system", content="late"))
            q.put_nowait(None)

        task = asyncio.ensure_future(feed())
        out = [c async for c in stream_room("r1", q, heartbeat=0.01)]
        await task
        return out

    got = asyncio.run(collect())
    assert got.count(": ping\n\n") >= 1
    assert "late" in got[-1]  # 心跳期间到达的事件照常送达


def test_stalled_subscriber_is_dropped_and_others_unaffected():
    """读不动的订阅者积压触顶后被终止（投 None → 前端重连全量对齐），不拖垮同房其他人。"""
    hub = RoomHub()
    stalled = hub.subscribe("r1")
    healthy = hub.subscribe("r1")

    for i in range(MAX_PENDING_CHUNKS + 1):
        hub.broadcast("r1", RoomEvent(type="system", content=f"c{i}"))
        healthy.get_nowait()  # 健康连接持续消费

    assert stalled.get_nowait() is None  # 积压被清空，只剩终止信号
    assert stalled.empty()
    after = RoomEvent(type="system", content="after")
    hub.broadcast("r1", after)
    assert healthy.get_nowait() is after


def test_inflight_replay_is_capped_to_queue_capacity():
    """超长生成的 in-flight buffer 重放不能撑爆新订阅者的有界队列。"""
    hub = RoomHub()
    hub.begin_generation("r1")
    for i in range(MAX_PENDING_CHUNKS + 10):
        hub.broadcast("r1", RoomEvent(type="system", content=f"c{i}"))

    late = hub.subscribe("r1")  # 不应抛 QueueFull
    assert late.qsize() == MAX_PENDING_CHUNKS
    assert late.get_nowait().content == "c10"  # 只重放尾部，更早的内容客户端从 DB 拉


def test_broadcast_rejects_raw_strings():
    """拒收裸字符串：防止再出现绕开 make_chunk、自己拼 SSE 的旁路（战斗/追逐曾经如此）。"""
    import pytest

    hub = RoomHub()
    hub.subscribe("r1")
    with pytest.raises(TypeError, match="只接受 RoomEvent"):
        hub.broadcast("r1", 'data: {"type":"combat_state"}\n\n')
