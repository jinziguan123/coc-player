"""规则内核（app.rules.kernel）：组件组装、冲突检测、村规自动注入、缓存与读时查找。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import create_model

from app.rules import RulesetError, get_system, ruleset_for
from app.rules.config import RuleConfig
from app.rules.kernel import Component, Provide, RuleSystem

# ── 官方预设 ──────────────────────────────────────────────────────────────


def test_official_preset_assembles_all_slots():
    rs = ruleset_for("coc")
    # 常驻组件 13 个；可选的燃运重骰、幸运消费默认关，技能成长默认开
    assert "coc7.growth" in rs.components
    assert "coc7.luck-spend" not in rs.components and "coc7.luck-reroll" not in rs.components
    for slot in (
        "dice", "character", "occupation", "equipment", "check",
        "sanity", "madness", "wound", "combat", "positioning", "chase",
        "growth", "luck",
    ):
        getattr(rs, slot)  # 不抛即在


def test_has_reports_slot_presence():
    assert ruleset_for("coc").has("growth") and not ruleset_for("coc").has("magic")


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
    monkeypatch.setattr("app.rules.coc.components.check.roll_percentile", lambda: 3)
    data = {"skills": {"侦查": 50}}
    assert ruleset_for("coc").check.resolve_skill_check(data, "侦查").outcome != "critical_success"
    loose = ruleset_for("coc", {"critical_max": 5})
    assert loose.check.resolve_skill_check(data, "侦查").outcome == "critical_success"


def test_caller_may_not_pass_options():
    with pytest.raises(TypeError, match="自动注入"):
        ruleset_for("coc").check.resolve_skill_check({}, "侦查", options=None)


def test_plain_exports_pass_through_unchanged():
    from app.rules.coc.components import wound

    assert ruleset_for("coc").wound.CHARACTER_STATUSES is wound.CHARACTER_STATUSES


# ── 缓存与读时查找 ────────────────────────────────────────────────────────


def test_same_options_share_one_ruleset():
    assert ruleset_for("coc") is ruleset_for("coc", {})
    assert ruleset_for("coc", {"critical_max": 5}) is ruleset_for("coc", {"critical_max": 5})
    assert ruleset_for("coc", {"critical_max": 5}) is not ruleset_for("coc")


def test_unknown_option_keys_are_dropped_before_caching():
    assert ruleset_for("coc", {"no_such_rule": 1}) is ruleset_for("coc")


def test_lookup_is_live_so_monkeypatch_reaches_cached_ruleset(monkeypatch):
    rs = ruleset_for("coc")
    monkeypatch.setattr("app.rules.coc.components.combat.is_active", lambda p: "patched")
    assert rs.combat.is_active({}) == "patched"


def test_describe_lists_only_changed_rules():
    assert ruleset_for("coc").describe() == []
    assert any("大成功" in line for line in ruleset_for("coc", {"critical_max": 5}).describe())


# ── 组装校验（用一套玩具规则系统）────────────────────────────────────────

_mod = SimpleNamespace(f=lambda options=None: options, g=lambda: "g")


def _cfg(*keys: str, flag: str | None = None) -> type[RuleConfig]:
    fields = {k: (int, 0) for k in keys}
    if flag:
        fields[flag] = (bool, False)
    return create_model("ToyConfig_" + "_".join((*keys, flag or "")), __base__=RuleConfig, **fields)


_TOY_OPTIONS = create_model("ToyOptions", __base__=RuleConfig, k=(int, 0))


def _system(*components: Component, preset=None, options_model=_TOY_OPTIONS) -> RuleSystem:
    return RuleSystem(
        id="toy", title="toy", version=1,
        components=components,
        preset=tuple(preset if preset is not None else (c.id for c in components)),
        options_model=options_model,
    )


def _comp(cid: str, slot: str = "", keys: tuple[str, ...] = (), **kw) -> Component:
    return Component(
        id=cid, title=cid, provides={slot or cid: Provide(_mod, plain=("g",))},
        config=_cfg(*keys) if keys else None, **kw,
    )


def test_toy_system_injects_its_own_options():
    comp = Component(id="a", title="a", config=_cfg("k"),
                     provides={"a": Provide(_mod, bound=("f",))})
    assert _system(comp).ruleset({"k": 1}).a.f().k == 1


def test_optional_component_follows_its_toggle():
    model = create_model("ToyFlagOptions", __base__=RuleConfig, k=(int, 0), on=(bool, False))
    comp = Component(id="opt", title="opt", config=_cfg(flag="on"), toggle="on")
    system = _system(_comp("a", keys=("k",)), comp, options_model=model)
    assert "opt" not in system.ruleset().components
    assert "opt" in system.ruleset({"on": True}).components


@pytest.mark.parametrize(("components", "preset", "message"), [
    ((_comp("a", requires=("b",), keys=("k",)),), None, "依赖 b，但它没有启用"),
    ((_comp("a", conflicts=("b",), keys=("k",)), _comp("b")), None, "互斥"),
    ((_comp("a", "s", keys=("k",)), _comp("b", "s")), None, "服务槽位 s 同时由 a 与 b 提供"),
    ((_comp("a", keys=("k",)), _comp("b", keys=("k",))), None, "村规键 k 同时归 a 与 b"),
    ((_comp("a"),), None, "村规键 k 没有任何组件认领"),
    ((_comp("a", keys=("k", "zzz")),), None, "未知的村规键 zzz"),
    ((_comp("a", keys=("k",)),), ("a", "ghost"), "不存在的组件：ghost"),
    ((_comp("a", keys=("k",)), _comp("a")), None, "组件 id 重复"),
    ((_comp("a", keys=("k",), toggle="nope"),), None, "开关 nope 不是它自己的参数"),
    ((_comp("a", keys=("k",), toggle="k"),), None, "必须是布尔参数"),
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
