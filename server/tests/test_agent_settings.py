"""agent 高级设置：存储、优先级、校验、恢复默认，以及各读取点确实用上了设置值。"""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import agent_settings
from app.ai import context as kp_context
from app.ai import turn_planner
from app.api.deps import require_local_client
from app.config import settings
from app.main import app
from app.services import kp_tool_loop
from tests._kp_context_corpus import _char, _module, _session


def _file() -> dict:
    return json.loads(agent_settings.SETTINGS_FILE.read_text("utf-8"))


# ── 存储与优先级 ─────────────────────────────────────────────────────

def test_没有配置文件时按环境与代码默认():
    current = agent_settings.current()
    assert current.generation_max_calls == settings.generation_max_calls
    assert current.narration_buffer_on_failure == settings.narration_buffer_on_failure
    assert current.planner_timeout_seconds == 120.0 and current.kp_tool_loop_max_steps == 20
    assert current.context_preset == "balanced" and current.overrides == {}


def test_配置文件覆盖优先于环境_环境优先于代码默认(monkeypatch):
    monkeypatch.setattr(settings, "generation_max_calls", 30)
    assert agent_settings.current().generation_max_calls == 30
    agent_settings.update({"generation_max_calls": 64})
    assert agent_settings.current().generation_max_calls == 64
    agent_settings.reset("generation_max_calls")
    assert agent_settings.current().generation_max_calls == 30


def test_只存改过的项_改回默认值即删除覆盖():
    agent_settings.update({"generation_max_calls": 64, "checks_use_fast_model": False},
                          context_preset="economy")
    assert _file() == {"version": 1, "context_preset": "economy",
                       "overrides": {"generation_max_calls": 64, "checks_use_fast_model": False}}
    agent_settings.update({"generation_max_calls": settings.generation_max_calls})
    assert _file()["overrides"] == {"checks_use_fast_model": False}


@pytest.mark.parametrize(("changes", "preset", "reason"), [
    ({"generation_max_calls": 3}, None, "8–200"),
    ({"generation_max_calls": 10.5}, None, "整数"),
    ({"generation_max_calls": True}, None, "数字"),
    ({"planner_timeout_seconds": "很长"}, None, "数字"),
    ({"checks_use_fast_model": 1}, None, "开或关"),
    ({"narration_buffer_on_failure": "drop"}, None, "release"),
    ({"no_such_key": 1}, None, "未知设置项"),
    ({}, "huge", "上下文档位"),
])
def test_非法值整体拒绝_文件不变(changes, preset, reason):
    agent_settings.update({"generation_max_calls": 64})
    before = _file()
    with pytest.raises(ValueError, match=reason):
        agent_settings.update({"kp_tool_loop_max_steps": 10, **changes}, context_preset=preset)
    assert _file() == before


def test_文件损坏或单项越界时回落默认_不改写原文件():
    agent_settings.SETTINGS_FILE.write_text("{坏掉的 json", "utf-8")
    assert agent_settings.current().generation_max_calls == settings.generation_max_calls
    assert agent_settings.SETTINGS_FILE.read_text("utf-8") == "{坏掉的 json"
    agent_settings.SETTINGS_FILE.write_text(json.dumps({
        "context_preset": "weird",
        "overrides": {"generation_max_calls": 9999, "kp_tool_loop_max_steps": 8},
    }), "utf-8")
    current = agent_settings.current()
    assert current.generation_max_calls == settings.generation_max_calls   # 越界项回落
    assert current.kp_tool_loop_max_steps == 8                            # 其余照常生效
    assert current.context_preset == "balanced"


def test_未知键在更新与恢复默认时原样保留():
    agent_settings.SETTINGS_FILE.write_text(json.dumps({
        "version": 1, "future_field": {"a": 1}, "overrides": {"future_param": 5},
    }), "utf-8")
    agent_settings.update({"generation_max_calls": 64})
    agent_settings.reset()
    data = _file()
    assert data["future_field"] == {"a": 1} and data["overrides"] == {"future_param": 5}
    assert data["context_preset"] == "balanced"


def test_全部恢复默认清空覆盖并回到均衡档_单项恢复只动该项():
    agent_settings.update({"generation_max_calls": 64, "kp_tool_loop_max_steps": 10},
                          context_preset="full")
    agent_settings.reset("kp_tool_loop_max_steps")
    assert agent_settings.current().overrides == {"generation_max_calls": 64}
    agent_settings.reset("context_preset")
    assert agent_settings.current().context_preset == "balanced"
    agent_settings.reset()
    assert agent_settings.current().overrides == {}


def test_写入是原子的_不留临时文件_外部修改下次读取即生效():
    agent_settings.update({"generation_max_calls": 64})
    assert [p.name for p in agent_settings.SETTINGS_FILE.parent.iterdir()
            if p.name.startswith(".agent_settings.")] == []
    data = _file()
    data["overrides"]["generation_max_calls"] = 70
    agent_settings.SETTINGS_FILE.write_text(json.dumps(data), "utf-8")
    import os
    os.utime(agent_settings.SETTINGS_FILE, (1, 2))   # 确保修改时间变化
    assert agent_settings.current().generation_max_calls == 70


# ── 读取点 ───────────────────────────────────────────────────────────

def test_生成预算按设置值进入调用预算(monkeypatch):
    import asyncio

    from app.services import generation_manager as gm

    seen = {}
    real_scope = gm.call_budget.scope

    def spy(**kw):
        seen.update(kw)
        return real_scope(**kw)

    monkeypatch.setattr(gm.call_budget, "scope", spy)
    agent_settings.update({"generation_max_calls": 64, "generation_max_seconds": 300})

    async def run():
        async def noop():
            return None
        manager = gm.GenerationManager()
        await manager.start("room-x", noop())

    asyncio.run(run())
    assert seen["max_calls"] == 64 and seen["seconds"] == 300.0


def test_规划器超时与工具循环步数_设置优先_未设置时用模块常量(monkeypatch):
    monkeypatch.setattr(turn_planner, "PLANNER_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(kp_tool_loop, "MAX_TOOL_LOOP_STEPS", 7)
    assert turn_planner.planner_timeout_seconds() == 0.2
    assert kp_tool_loop.tool_loop_max_steps() == 7
    agent_settings.update({"planner_timeout_seconds": 300, "kp_tool_loop_max_steps": 12})
    assert turn_planner.planner_timeout_seconds() == 300.0
    assert kp_tool_loop.tool_loop_max_steps() == 12


@pytest.mark.parametrize(("preset", "large", "small"), [
    ("economy", 64_000, 48_000), ("balanced", 150_000, 48_000), ("full", 200_000, 48_000),
])
def test_上下文档位决定事件预算上限_小窗口三档相同(preset, large, small):
    agent_settings.update({}, context_preset=preset)
    assert kp_context.resolve_context_budget(1_000_000) == large
    assert kp_context.resolve_context_budget(64_000) == small


def test_均衡档等于现有常量_改常量即生效(monkeypatch):
    monkeypatch.setattr(kp_context, "MIN_RECENT_EVENTS", 4)
    values = kp_context.context_preset_values("balanced")
    assert (values.budget_ceiling, values.min_recent_events, values.max_summary_tokens,
            values.visited_scene_window) == (150_000, 4, 2000, 6)


@pytest.mark.parametrize(("preset", "visible"), [
    ("economy", {"s7", "s8", "s9"}), ("balanced", {"s4", "s5", "s6", "s7", "s8", "s9"}),
])
def test_上下文档位决定保留几个去过的场景(preset, visible):
    agent_settings.update({}, context_preset=preset)
    session = SimpleNamespace(navigation=SimpleNamespace(visited_scenes=[f"s{i}" for i in range(10)]),
                              world_state={})
    module = SimpleNamespace(clues=[])
    assert kp_context._visible_scenes(session, "s9", module) == visible


def _recent_event_messages(preset: str) -> int:
    from app.models import EventLog

    agent_settings.update({}, context_preset=preset)
    hero = _char("调查员")
    events = []
    for i in range(1, 41):
        is_kp = i % 2 == 0
        events.append(EventLog(
            id=f"e{i}", session_id="sess-1", sequence_num=i,
            event_type="narration" if is_kp else "action",
            actor_id=None if is_kp else hero.id, actor_name="KP" if is_kp else hero.name,
            content=f"第{i}条{'旁白' if is_kp else '行动'}", metadata_={},
        ))
    messages = kp_context.build_kp_context(_session(), _module(), hero, events, context_budget=1000)
    return sum(1 for m in messages if m.get("role") != "system" and "第" in str(m.get("content"))
               and ("旁白" in str(m.get("content")) or "行动" in str(m.get("content"))))


def test_预算很紧时最近原文保底随档位变化():
    economy, balanced, full = (_recent_event_messages(p) for p in ("economy", "balanced", "full"))
    assert economy < balanced < full
    assert balanced == kp_context.MIN_RECENT_EVENTS


# ── 接口 ─────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    app.dependency_overrides[require_local_client] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_接口读写与恢复默认(client):
    view = client.get("/api/settings/agent").json()
    keys = [p["key"] for p in view["params"]]
    assert "generation_max_calls" in keys and "kp_tool_loop_max_steps" in keys
    assert view["context_preset"] == "balanced"
    assert [p["key"] for p in view["context_presets"]] == ["economy", "balanced", "full"]
    economy = view["context_presets"][0]
    assert (economy["budget_ceiling"], economy["min_recent_events"]) == (64_000, 6)

    r = client.put("/api/settings/agent", json={"values": {"generation_max_calls": 64},
                                               "context_preset": "full"})
    assert r.status_code == 200
    param = next(p for p in r.json()["params"] if p["key"] == "generation_max_calls")
    assert param["value"] == 64 and param["overridden"] and param["default"] == settings.generation_max_calls
    assert r.json()["context_preset"] == "full"

    bad = client.put("/api/settings/agent", json={"values": {"generation_max_calls": 1}})
    assert bad.status_code == 422 and "8–200" in bad.json()["detail"]

    r = client.delete("/api/settings/agent", params={"key": "generation_max_calls"})
    assert not next(p for p in r.json()["params"] if p["key"] == "generation_max_calls")["overridden"]
    r = client.delete("/api/settings/agent")
    assert r.json()["context_preset"] == "balanced"


def test_接口仅限本机():
    app.dependency_overrides.clear()
    remote = TestClient(app, client=("8.8.8.8", 1234))
    assert remote.get("/api/settings/agent").status_code in (401, 403)
