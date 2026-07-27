"""测试辅助：把 ``RoomEvent`` 还原成线上 SSE 文本。

事件类型收口（``app/services/room_events.py``）之后，业务代码产出的是 ``RoomEvent``
对象而不再是 SSE 字符串。已有断言大多是「广播出去的负载里含不含某段文字/某个键」，
按线上文本匹配才是它们原本要验证的东西——所以这里保留字符串形态，而不是把断言
逐条改写成结构化，避免在一次机械迁移里悄悄改变测试意图。

新写的断言优先用结构化形式（``c.type == "dice"``、``c.as_wire()["metadata"]``），
更准确也更好读。
"""

from app.services.room_hub import encode_sse


def wire(chunk) -> str:
    """单条事件 → SSE 文本。"""
    return encode_sse(chunk)


def wires(chunks) -> list[str]:
    """一串事件 → SSE 文本列表。"""
    return [encode_sse(c) for c in chunks]
