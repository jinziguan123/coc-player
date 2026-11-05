"""存量模组升级到 v3 的纯函数测试。"""

from app.services import module_flow as mf


def _legacy():
    return {
        "title": "旧模组",
        "world_setting": {"era": "现代", "intro": "开场氛围"},
        "scenes": [
            {"id": "scene_1", "title": "起点", "kind": "location",
             "connections": ["scene_2"]},
            {"id": "scene_ev", "title": "推进", "kind": "event",
             "when": "玩家打开门", "connections": ["scene_2"],
             "events": [{"trigger": "开门", "kind": "san_check", "san_loss": "0/1"}]},
            {"id": "scene_2", "title": "终点", "kind": "location"},
        ],
        "endings": [{"id": "end_1", "name": "结局", "when": "玩家走进光里"}],
        "npcs": [],
        "clues": [],
        "triggers": [
            {"id": "trig_1", "when": "玩家打开门", "set_flags": ["door_open"],
             "clear_flags": [], "description": "门开了"},
        ],
    }


def test_upgrade_promotes_events_and_splits_locations():
    out = mf.upgrade_legacy_module(_legacy())
    assert [s["id"] for s in out["scenes"]] == ["scene_1", "scene_2"]
    assert "scene_ev" in {e["id"] for e in out["events"]}
    ev = next(e for e in out["events"] if e["id"] == "scene_ev")
    assert ev["at"] == "scene_2"
    assert any(e.get("type") == "sanity" for e in ev["effects"])


def test_upgrade_merges_triggers_into_events():
    out = mf.upgrade_legacy_module(_legacy())
    trigger = next(e for e in out["events"] if e["id"] == "trig_1")
    assert any(
        eff.get("type") == "set_flag" and eff.get("flag") == "door_open"
        for eff in trigger["effects"]
    )
    assert out["triggers"] == []


def test_upgrade_fills_start_and_wraps_ending_when():
    out = mf.upgrade_legacy_module(_legacy())
    assert out["world"]["start"] == "scene_1"
    assert out["endings"][0]["when"] == {"text": "玩家走进光里", "expr": None}
    assert out["schema_version"] == 3
    assert "validation" in out


def test_upgrade_is_idempotent():
    once = mf.upgrade_legacy_module(_legacy())
    twice = mf.upgrade_legacy_module(once)
    assert twice["scenes"] == once["scenes"]
    assert twice["events"] == once["events"]
    assert twice["endings"] == once["endings"]
    assert twice["world"]["start"] == once["world"]["start"]
