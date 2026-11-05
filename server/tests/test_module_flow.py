"""模组 v3 事件内核的纯函数测试：条件三值求值、效果校验、事件规整与整份校验。"""

from app.services import module_flow as mf


def _state(**kw):
    return mf.FlowState(**kw)


# ── 条件 ──────────────────────────────────────────────────────────────────


def test_condition_atoms_and_three_valued_logic():
    state = _state(
        flags={"door": True}, at="scene_1", visited=frozenset({"scene_1"}),
        clues=frozenset({"clue_1"}), items={"手电筒": 1}, phase="scene",
    )
    assert mf.evaluate_condition({"flag": "door"}, state) is True
    assert mf.evaluate_condition({"flag": "window"}, state) is False
    assert mf.evaluate_condition({"at": "scene_1"}, state) is True
    assert mf.evaluate_condition({"visited": "scene_1"}, state) is True
    assert mf.evaluate_condition({"clue_known": "clue_1"}, state) is True
    assert mf.evaluate_condition({"has_item": "手电筒"}, state) is True
    assert mf.evaluate_condition({"phase": "scene"}, state) is True
    assert mf.evaluate_condition({"unknown_atom": "x"}, state) is None
    assert mf.evaluate_condition(None, state) is None


def test_condition_combinators_propagate_unknown():
    state = _state(flags={"a": True})
    assert mf.evaluate_condition({"all": [{"flag": "a"}, {"flag": "missing"}]}, state) is False
    assert mf.evaluate_condition({"any": [{"flag": "a"}, {"flag": "missing"}]}, state) is True
    # 一个未知原子会让 all 变成「判不了」，而不是 False
    assert mf.evaluate_condition({"all": [{"flag": "a"}, {"mystery": "x"}]}, state) is None
    assert mf.evaluate_condition({"not": {"flag": "a"}}, state) is False
    assert mf.evaluate_condition({"not": {"mystery": "x"}}, state) is None


def test_validate_condition_rejects_bad_shape():
    assert mf.validate_condition(None) == []
    assert mf.validate_condition({"all": []})
    assert mf.validate_condition({"flag": ""})
    assert mf.validate_condition({"unknown": "x"})
    assert mf.validate_condition({"all": [{"flag": "ok"}]}) == []


# ── 效果 ──────────────────────────────────────────────────────────────────


def test_validate_effect_types_and_nested_check():
    assert mf.validate_effect({"type": "set_flag", "flag": "x"}) == []
    assert mf.validate_effect({"type": "set_flag"})
    assert mf.validate_effect({"type": "bogus"})
    bad = {"type": "check", "skill": "侦查", "on_success": [{"type": "goto"}]}
    errors = mf.validate_effect(bad)
    assert any("on_success[0].target" in e for e in errors)
    ok = {
        "type": "check", "skill": "侦查",
        "on_success": [{"type": "goto", "target": "scene_2"}],
        "on_failure": [{"type": "narration", "text": "什么也没找到"}],
    }
    assert mf.validate_effect(ok) == []


def test_effect_brief_is_readable():
    brief = mf.effect_brief({
        "type": "check", "skill": "侦查", "difficulty": "普通",
        "on_success": [{"type": "goto", "target": "scene_top"}],
        "on_failure": [{"type": "narration", "text": "身体拒绝前行"}],
    })
    assert "侦查" in brief and "scene_top" in brief and "身体拒绝前行" in brief


# ── 事件规整 ──────────────────────────────────────────────────────────────


def test_normalize_event_keeps_v3_fields():
    ev = mf.normalize_event({
        "id": "ev_1", "title": "朝山顶走", "at": "scene_1", "phase": "scene",
        "when": {"text": "玩家试图上山", "expr": {"at": "scene_1"}},
        "effects": [{"type": "goto", "target": "scene_top"}],
    })
    assert ev["id"] == "ev_1" and ev["at"] == "scene_1"
    assert ev["when"]["expr"] == {"at": "scene_1"}
    assert ev["once"] is True


def test_normalize_events_promotes_legacy_event_scene():
    scenes = [{
        "id": "scene_ev_dream", "title": "梦（导入前）", "kind": "event",
        "when": "模组开始前（开场时朗读）", "connections": ["scene_1"],
        "events": [{"trigger": "经历这场梦", "kind": "san_check", "san_loss": "5+1d5"}],
    }]
    events = mf.normalize_events([], scenes)
    assert len(events) == 1
    ev = events[0]
    assert ev["id"] == "scene_ev_dream" and ev["at"] == "scene_1"
    assert ev["phase"] == "opening"
    assert any(e.get("type") == "sanity" and e.get("loss") == "5+1d5" for e in ev["effects"])


def test_legacy_dice_check_becomes_check_with_note():
    ev = mf.normalize_event({
        "id": "ev_x", "when": "x", "kind": "dice_check", "skill": "意志",
        "note": "成功则前往山顶",
    })
    types = [e["type"] for e in ev["effects"]]
    assert "check" in types and "narration" in types


# ── 可触发事件 ────────────────────────────────────────────────────────────


def test_eligible_events_respects_seen_and_unknown():
    events = [
        {"id": "e1", "when": {"text": "", "expr": {"at": "scene_1"}}, "effects": [], "once": True},
        {"id": "e2", "when": {"text": "", "expr": {"flag": "gone"}}, "effects": [], "once": True},
        {"id": "e3", "when": {"text": "由 KP 判断", "expr": None}, "effects": [], "once": True},
        {"id": "e4", "when": {"text": "", "expr": {"at": "scene_1"}}, "effects": [], "once": True},
    ]
    state = _state(at="scene_1", seen=frozenset({"e4"}))
    eligible, undecided = mf.eligible_events(events, state)
    assert [e["id"] for e in eligible] == ["e1"]
    assert [e["id"] for e in undecided] == ["e3"]


# ── 整份校验 ──────────────────────────────────────────────────────────────


def test_validate_module_reports_broken_refs_and_dangling_flags():
    data = {
        "world": {"start": "scene_1"},
        "locations": [
            {"id": "scene_1", "name": "醒来的地方", "exits": [{"to": "scene_2"}]},
            {"id": "scene_2", "name": "岔路口", "exits": [{"to": "missing_scene"}]},
        ],
        "events": [
            {"id": "ev_good", "at": "scene_1",
             "when": {"text": "", "expr": {"flag": "door_open"}},
             "effects": [{"type": "set_flag", "flag": "door_open"}]},
            {"id": "ev_orphan", "at": "scene_2",
             "when": {"text": "KP 自己判断", "expr": None},
             "effects": [{"type": "set_flag", "flag": "orphan_flag"}]},
            {"id": "ev_bad_target", "at": "scene_2",
             "effects": [{"type": "goto", "target": "nowhere"}]},
        ],
        "endings": [{"id": "end_1", "name": "结局", "when": {"text": "走到终点"}}],
    }
    report = mf.validate_module(data)
    codes = {e["code"] for e in report["errors"]}
    wcodes = {w["code"] for w in report["warnings"]}
    assert "BROKEN_EXIT" in codes
    assert "BROKEN_REF" in codes
    assert "DANGLING_FLAG" in wcodes


def test_validate_module_flags_unreachable_locations():
    data = {
        "world": {"start": "scene_1"},
        "locations": [
            {"id": "scene_1", "exits": [{"to": "scene_2"}]},
            {"id": "scene_2", "exits": []},
            {"id": "scene_island", "exits": []},
        ],
        "events": [],
        "endings": [],
    }
    report = mf.validate_module(data)
    assert any(w["code"] == "UNREACHABLE_LOCATION" and w["ref"] == "scene_island"
               for w in report["warnings"])
