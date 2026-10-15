"""插件给 KP 注册工具：声明校验、重名拒载、随组件开关进出、schema 与工具模式说明、执行与异常隔离。"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.ai import profile_store
from app.ai import tools as kp_tools
from app.ai.provider import ToolCall
from app.rules import get_system, plugins, ruleset_for
from app.rules.kernel import Component, RulesetError, register_system
from app.rules.kp_tools import KpTool, KpToolResult
from app.services import chat_service, session_service
from tests.test_kp_tool_loop import (  # noqa: F401 — db_factory 是夹具
    _call,
    _FakeToolLLM,
    _patch_runtime,
    _Profile,
    _seed,
    _text,
    db_factory,
)

EXAMPLE = Path(__file__).resolve().parents[2] / "docs/plugins/random_encounter.py"

PLUGIN = '''
from app.rules.config import RuleConfig, rule_field
from app.rules.kernel import Component
from app.rules.kp_tools import KpTool
PLUGIN_API_VERSION = 1
RULE_SYSTEM = "coc"
NAME = "{name}"
class C(RuleConfig):
    {key}: bool = rule_field(False, title="开关")
COMPONENTS = [Component(
    id="{cid}", title="{name}", config=C, toggle="{key}", group="探索",
    kp_tools=(KpTool(name="{tool}", description="测试工具",
                     parameters={{"type": "object", "properties": {{}}}},
                     execute=lambda ctx, args: "ok"),),
)]
'''


@pytest.fixture(autouse=True)
def restore_official():
    original = get_system("coc")
    yield
    register_system(original, replace=True)
    plugins._INFOS.clear()
    plugins._OFFICIAL.clear()


def _by_file(infos):
    return {i.file: i for i in infos["coc"]}


def _tool(name="my_tool", execute=lambda ctx, args: "ok", **kw) -> KpTool:
    return KpTool(
        name=name, description=kw.get("description", "测试工具"),
        parameters=kw.get("parameters", {"type": "object", "properties": {}}), execute=execute,
    )


# ── 加载与校验 ──────────────────────────────────────────────────────────────


def test_example_plugin_tool_follows_component_toggle(tmp_path):
    shutil.copy(EXAMPLE, tmp_path / "random_encounter.py")
    info = _by_file(plugins.install_all(tmp_path))["random_encounter.py"]
    assert info.status == "loaded", info.error

    assert ruleset_for("coc").kp_tools == ()                        # 装上不等于启用
    on = ruleset_for("coc", {"random_encounter": True})
    assert [t.name for t in on.kp_tools] == ["roll_encounter"]


def test_plugin_tool_clashing_with_builtin_is_rejected(tmp_path):
    (tmp_path / "clash.py").write_text(
        PLUGIN.format(name="撞内置", key="clash_flag", cid="me.clash", tool="dice_check"),
        encoding="utf-8",
    )
    info = _by_file(plugins.install_all(
        tmp_path, reserved_tool_names=kp_tools.TOOLS_BY_NAME))["clash.py"]
    assert info.status == "failed"
    assert "dice_check" in info.error and "内置工具" in info.error
    assert "me.clash" not in {c.id for c in get_system("coc").components}


def test_two_plugins_with_same_tool_name_second_is_rejected(tmp_path):
    (tmp_path / "a_first.py").write_text(
        PLUGIN.format(name="甲", key="a_flag", cid="me.a", tool="shared_tool"), encoding="utf-8")
    (tmp_path / "b_second.py").write_text(
        PLUGIN.format(name="乙", key="b_flag", cid="me.b", tool="shared_tool"), encoding="utf-8")
    infos = _by_file(plugins.install_all(tmp_path))
    assert infos["a_first.py"].status == "loaded"
    assert infos["b_second.py"].status == "failed" and "shared_tool" in infos["b_second.py"].error


def test_malformed_tool_declaration_fails_assembly():
    base = get_system("coc")
    from app.rules.config import RuleConfig, rule_field

    class C(RuleConfig):
        bad_tool_flag: bool = rule_field(False, title="开关")

    comp = Component(
        id="me.bad", title="坏", config=C, toggle="bad_tool_flag",
        kp_tools=(_tool(name="Bad-Name", description="", parameters={"type": "array"}),),
    )
    with pytest.raises(RulesetError) as e:
        plugins._extend(base, [comp])
    msg = str(e.value)
    assert "snake_case" in msg and "缺少说明" in msg and "type=object" in msg


# ── schema 与工具模式说明 ─────────────────────────────────────────────────────


def test_schemas_and_mode_message_include_plugin_tools():
    t = _tool(name="roll_encounter", description="掷一次随机遭遇。\n第二行细节")
    names = [s["function"]["name"] for s in kp_tools.openai_tool_schemas(extra=[t])]
    assert names[-1] == "roll_encounter" and "dice_check" in names
    msg = kp_tools.tool_mode_message([t])["content"]
    assert "roll_encounter：掷一次随机遭遇。" in msg and "第二行细节" not in msg


def test_mode_message_unchanged_without_plugin_tools():
    """无插件工具时说明逐字不变：不污染无插件局的提示词。"""
    assert kp_tools.tool_mode_message() == kp_tools.tool_mode_message(())
    assert "规则插件" not in kp_tools.tool_mode_message()["content"]


# ── 执行 ─────────────────────────────────────────────────────────────────────


def _executor(*tools):
    rs = SimpleNamespace(kp_tools=tools, options=None)
    return chat_service._build_kp_tool_executor(
        None, "s1", None, None, SimpleNamespace(name="伊芙琳"), [], llm=None,
        result=["", "", [], [], []], ruleset=rs,
    ), rs


def test_plugin_tool_executes_sync_async_and_suspend():
    seen = {}

    def sync_tool(ctx, args):
        seen["ctx"], seen["args"] = ctx, args
        return "同步结果"

    async def async_tool(ctx, args):
        return KpToolResult("等玩家先做决定", suspend=True)

    execute, rs = _executor(_tool("sync_tool", sync_tool), _tool("async_tool", async_tool))

    async def _go():
        a = await execute(ToolCall(id="1", name="sync_tool", arguments={"n": 3}))
        b = await execute(ToolCall(id="2", name="async_tool", arguments={}))
        return a, b

    a, b = asyncio.run(_go())
    assert a.result_text == "同步结果" and not a.suspend
    assert seen["args"] == {"n": 3}                     # 参数原样（不像内置工具那样转成字符串）
    assert seen["ctx"].ruleset is rs and seen["ctx"].session_id == "s1"
    assert b.result_text == "等玩家先做决定" and b.suspend


def test_plugin_tool_exception_does_not_break_turn():
    def boom(ctx, args):
        raise RuntimeError("插件坏了")

    execute, _ = _executor(_tool("boom_tool", boom))
    out = asyncio.run(execute(ToolCall(id="1", name="boom_tool", arguments={})))
    assert "执行出错" in out.result_text and not out.suspend


def test_plugin_cannot_shadow_builtin_at_runtime():
    """即便绕过加载校验：schema 与说明里不出现重名项，执行器也只按内置工具分发。"""
    shadow = _tool("set_mood", lambda ctx, args: "插件抢答")
    names = [s["function"]["name"] for s in kp_tools.openai_tool_schemas(extra=[shadow])]
    assert names.count("set_mood") == 1
    assert "规则插件" not in kp_tools.tool_mode_message([shadow])["content"]
    execute, _ = _executor(shadow)
    out = asyncio.run(execute(ToolCall(id="1", name="set_mood", arguments={"mood": "nope"})))
    assert out.result_text != "插件抢答"


# ── 端到端：主回合的工具循环 ──────────────────────────────────────────────────


def test_turn_offers_and_runs_enabled_plugin_tool(db_factory, monkeypatch, tmp_path):  # noqa: F811
    shutil.copy(EXAMPLE, tmp_path / "random_encounter.py")
    plugins.install_all(tmp_path)

    llm = _FakeToolLLM([
        [_call("roll_encounter", {"where": "wild"})],
        [_text("风从林间穿过。")],
    ])
    _patch_runtime(monkeypatch, db_factory, llm)
    monkeypatch.setattr(profile_store, "load_active_profile", lambda: _Profile(True))

    db = db_factory()
    session_id, session, module, hero = _seed(db)
    session.rule_options = {"random_encounter": True}
    db.commit()
    session_service.add_event(db, session_id, "action", "我们连夜赶路", actor_name="伊芙琳")
    events = session_service.get_session_events(db, session_id)

    asyncio.run(chat_service._run_generation(db, session_id, session, module, hero, events))

    names = {t["function"]["name"] for t in llm.calls[0]["tools"]}
    assert "roll_encounter" in names and "dice_check" in names
    sys_texts = [m["content"] for m in llm.calls[0]["messages"] if m["role"] == "system"]
    assert any("roll_encounter" in t for t in sys_texts if "工具调用模式" in t)
    tool_msgs = [m for m in llm.calls[1]["messages"] if m["role"] == "tool"]
    assert tool_msgs and "随机遭遇（1D6=" in tool_msgs[-1]["content"]
