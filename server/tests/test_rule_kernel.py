"""规则内核（app.rules.kernel）：组件组装、冲突检测、村规自动注入、缓存与读时查找。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.rules import RulesetError, get_system, ruleset_for
from app.rules.kernel import Component, Provide, RuleSystem

# ── 官方预设 ──────────────────────────────────────────────────────────────


def test_official_preset_assembles_all_slots():
    rs = ruleset_for("coc")
    assert len(rs.components) == 13
    for slot in (
        "dice", "sheet", "character", "occupation", "specialization", "equipment", "check",
        "sanity", "madness", "wound", "status", "combat", "weapons", "positioning", "chase",
        "growth", "luck",
    ):
        getattr(rs, slot)  # 不抛即在


def test_unknown_slot_is_readable_error():
    with pytest.raises(AttributeError, match="没有服务槽位 'magic'"):
        ruleset_for("coc").magic


def test_unknown_export_is_readable_error():
    with pytest.raises(AttributeError, match="没有导出 'nope'"):
        ruleset_for("coc").check.nope


def test_unknown_system_raises_value_error():
    with pytest.raises(ValueError, match="未注册的规则系统"):
        ruleset_for("dnd")


# ── 村规注入 ──────────────────────────────────────────────────────────────


def test_options_are_injected_into_bound_exports(monkeypatch):
    monkeypatch.setattr("app.rules.coc.checks.roll_percentile", lambda: 3)
    data = {"skills": {"侦查": 50}}
    assert ruleset_for("coc").check.resolve_skill_check(data, "侦查").outcome != "critical_success"
    loose = ruleset_for("coc", {"critical_max": 5})
    assert loose.check.resolve_skill_check(data, "侦查").outcome == "critical_success"


def test_caller_may_not_pass_options():
    with pytest.raises(TypeError, match="自动注入"):
        ruleset_for("coc").check.resolve_skill_check({}, "侦查", options=None)


def test_plain_exports_pass_through_unchanged():
    from app.rules.coc import weapons

    assert ruleset_for("coc").weapons.COC_WEAPONS is weapons.COC_WEAPONS


# ── 缓存与读时查找 ────────────────────────────────────────────────────────


def test_same_options_share_one_ruleset():
    assert ruleset_for("coc") is ruleset_for("coc", {})
    assert ruleset_for("coc", {"critical_max": 5}) is ruleset_for("coc", {"critical_max": 5})
    assert ruleset_for("coc", {"critical_max": 5}) is not ruleset_for("coc")


def test_unknown_option_keys_are_dropped_before_caching():
    assert ruleset_for("coc", {"no_such_rule": 1}) is ruleset_for("coc")


def test_lookup_is_live_so_monkeypatch_reaches_cached_ruleset(monkeypatch):
    rs = ruleset_for("coc")
    monkeypatch.setattr("app.rules.coc.combat.is_active", lambda p: "patched")
    assert rs.combat.is_active({}) == "patched"


def test_describe_lists_only_changed_rules():
    assert ruleset_for("coc").describe() == []
    assert any("大成功" in line for line in ruleset_for("coc", {"critical_max": 5}).describe())


# ── 组装校验（用一套玩具规则系统）────────────────────────────────────────

_mod = SimpleNamespace(f=lambda options=None: options, g=lambda: "g")


def _system(*components: Component, preset=None, option_fields=("k",)) -> RuleSystem:
    return RuleSystem(
        id="toy",
        components=components,
        preset=tuple(preset if preset is not None else (c.id for c in components)),
        option_fields=option_fields,
        parse_options=lambda raw: tuple(sorted((raw or {}).items())),
        merge_options=lambda *layers: {},
        describe_options=lambda o: [],
    )


def _comp(cid: str, slot: str = "", **kw) -> Component:
    return Component(id=cid, title=cid, provides={slot or cid: Provide(_mod, plain=("g",))}, **kw)


def test_toy_system_injects_its_own_options():
    comp = Component(id="a", title="a", option_keys=("k",),
                     provides={"a": Provide(_mod, bound=("f",))})
    assert _system(comp).ruleset({"k": 1}).a.f() == (("k", 1),)


@pytest.mark.parametrize(("components", "preset", "message"), [
    ((_comp("a", requires=("b",), option_keys=("k",)),), None, "依赖 b，但它没有启用"),
    ((_comp("a", conflicts=("b",), option_keys=("k",)), _comp("b")), None, "互斥"),
    ((_comp("a", "s", option_keys=("k",)), _comp("b", "s")), None, "服务槽位 s 同时由 a 与 b 提供"),
    ((_comp("a", option_keys=("k",)), _comp("b", option_keys=("k",))), None, "村规键 k 同时归 a 与 b"),
    ((_comp("a"),), None, "村规键 k 没有任何组件认领"),
    ((_comp("a", option_keys=("k", "zzz")),), None, "未知的村规键 zzz"),
    ((_comp("a", option_keys=("k",)),), ("a", "ghost"), "不存在的组件：ghost"),
    ((_comp("a", option_keys=("k",)), _comp("a")), None, "组件 id 重复"),
])
def test_assembly_errors_are_reported(components, preset, message):
    with pytest.raises(RulesetError, match=message):
        _system(*components, preset=preset)


def test_all_assembly_errors_reported_at_once():
    with pytest.raises(RulesetError) as exc:
        _system(_comp("a", requires=("x",)), _comp("b", requires=("y",)))
    text = str(exc.value)
    assert "依赖 x" in text and "依赖 y" in text and "没有任何组件认领" in text


def test_provide_rejects_missing_export():
    with pytest.raises(RulesetError, match="没有这些导出"):
        Provide(_mod, plain=("missing",))


def test_provide_rejects_name_in_both_groups():
    with pytest.raises(RulesetError, match="同时出现在 bound 与 plain"):
        Provide(_mod, bound=("g",), plain=("g",))


def test_registered_system_is_the_official_one():
    assert get_system("coc").id == "coc"
