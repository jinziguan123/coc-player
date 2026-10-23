"""文本指令适配层：消息改写、多指令解析、收尾轮不执行，以及编排层按配置启用。"""
import asyncio

from app.ai.provider import ToolCall
from app.ai.text_tool_adapter import TOOL_RESULT_PREFIX, TextToolAdapter, to_text_messages
from app.services import kp_tool_loop


class _Text:
    last_usage = {"total_tokens": 7}

    def __init__(self, text):
        self.text = text
        self.seen = []

    def supports_tools(self):
        return False

    async def complete(self, *a, **k):
        return "ok"

    async def stream(self, messages, **kw):
        self.seen.append(messages)
        yield self.text


def _collect(adapter, messages, tools):
    async def run():
        return [d async for d in adapter.stream_chat(messages, tools=tools)]
    return asyncio.run(run())


def test_多条文本指令按顺序转成工具调用():
    inner = _Text("门开了。[SET_FLAG: flag=door_opened]\n[SCENE_CHANGE: scene_id=b]")
    deltas = _collect(TextToolAdapter(inner, kp_tool_loop._tool_calls_from_text), [], [{"x": 1}])
    assert deltas[0].kind == "text"
    calls = [d.tool_call for d in deltas if d.kind == "tool_call"]
    assert [(c.name, c.arguments) for c in calls] == [
        ("set_flag", {"flag": "door_opened"}), ("scene_change", {"scene_id": "b"}),
    ]


def test_收尾轮不带工具时不执行残留指令():
    inner = _Text("收束。[SET_FLAG: flag=x]")
    deltas = _collect(TextToolAdapter(inner, kp_tool_loop._tool_calls_from_text), [], None)
    assert all(d.kind == "text" for d in deltas)


def test_工具结果回注改写成普通消息():
    messages = [
        {"role": "system", "content": "KP"},
        {"role": "assistant", "content": "[DICE_CHECK: skill=侦查]",
         "tool_calls": [{"id": "t", "function": {"name": "dice_check", "arguments": "{}"}}],
         "reasoning_content": "想"},
        {"role": "tool", "tool_call_id": "t", "content": "侦查成功"},
    ]
    out = to_text_messages(messages)
    assert out[1] == {"role": "assistant", "content": "[DICE_CHECK: skill=侦查]"}
    # 检定/查阅结果本身就是执行器写好的续写提示：原样回注，不加前缀
    assert out[2] == {"role": "user", "content": "侦查成功"}
    other = to_text_messages([
        {"role": "assistant", "content": "[SET_MOOD: mood=horror]",
         "tool_calls": [{"id": "m", "function": {"name": "set_mood", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "m", "content": "ok"},
    ])
    assert other[1] == {"role": "user", "content": f"{TOOL_RESULT_PREFIX}ok"}
    assert all("tool_calls" not in m for m in out)


def test_适配层透传用量与其他接口():
    inner = _Text("x")
    adapter = TextToolAdapter(inner, lambda t: [ToolCall(id="1", name="set_flag", arguments={})])
    assert adapter.last_usage == {"total_tokens": 7}
    assert adapter.supports_tools() and adapter.text_commands
    assert asyncio.run(adapter.complete([])) == "ok"


def test_编排层选择_原生优先_否则一律经适配层(monkeypatch):
    inner = _Text("x")
    monkeypatch.setattr(kp_tool_loop, "_tool_loop_active", lambda llm: False)
    adapter = kp_tool_loop.loop_llm(inner)
    assert isinstance(adapter, TextToolAdapter)
    assert kp_tool_loop.tool_mode_messages(adapter, []) == []   # 不要求纯文本模型改用工具
    monkeypatch.setattr(kp_tool_loop, "_tool_loop_active", lambda llm: True)
    assert kp_tool_loop.loop_llm(inner) is inner
    assert kp_tool_loop.loop_llm(None) is None

def test_开启适配层后纯文本模型的回合走工具循环执行全部指令(tmp_path, monkeypatch):
    import app.database as database
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.ai import profile_store
    from app.models import Base
    from app.services import chat_service, entity_state, session_service
    from app.services.room_hub import room_hub
    from tests.test_kp_dual_path_equivalence import _seed

    engine = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    class _Profile:
        use_tool_calls = False   # 房主关了工具调用

    calls = []

    class _TextModel(_Text):
        async def stream(self, messages, **kw):
            calls.append(messages)
            if len(calls) == 1:
                yield "门吱呀一声开了。[SET_FLAG: flag=door_opened]"
            else:
                yield "你们走了进去。"

    model = _TextModel("")
    monkeypatch.setattr(database, "SessionLocal", factory)
    monkeypatch.setattr(chat_service, "get_llm", lambda: model)
    monkeypatch.setattr(chat_service, "get_fast_llm", lambda: model)
    monkeypatch.setattr(room_hub, "broadcast", lambda *a, **k: None)
    monkeypatch.setattr(profile_store, "load_active_profile", lambda: _Profile())

    db = factory()
    session_id, session, module, hero = _seed(db)
    session_service.add_event(db, session_id, "action", "我推门", actor_id=hero.id, actor_name=hero.name)
    events = session_service.get_session_events(db, session_id)
    asyncio.run(chat_service._run_generation(db, session_id, session, module, hero, events))

    db.expire_all()
    assert "door_opened" in entity_state.active_flags(db.get(type(session), session_id))
    narr = [e.content for e in session_service.get_session_events(db, session_id) if e.event_type == "narration"]
    assert narr == ["门吱呀一声开了。"]          # 指令不进旁白
    assert len(calls) == 1                        # 标志是收尾型指令：与旧文本路径一致，不再续写
    assert not any("工具调用模式" in str(m.get("content", "")) for m in calls[0])


def test_只执行了状态指令时不再续写_检定与查阅结果才续写():
    from app.ai.text_tool_adapter import needs_continuation

    def turn(name):
        return [
            {"role": "user", "content": "我踢开门"},
            {"role": "assistant", "content": "[X]", "tool_calls": [
                {"id": "1", "function": {"name": name, "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "1", "content": "ok"},
        ]

    assert needs_continuation([{"role": "user", "content": "首轮"}])
    assert not needs_continuation(turn("start_combat"))
    assert not needs_continuation(turn("scene_change"))
    assert needs_continuation(turn("dice_check"))
    assert needs_continuation(turn("rule_lookup"))
    inner = _Text("不该被调用")
    deltas = _collect(TextToolAdapter(inner, kp_tool_loop._tool_calls_from_text), turn("set_flag"), [{"x": 1}])
    assert deltas == [] and inner.seen == []
