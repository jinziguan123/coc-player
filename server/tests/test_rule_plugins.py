"""P4：第三方规则插件——加载、失败隔离、冲突剔除、示例插件可用、钩子生效。"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.rules import get_system, plugins, ruleset_for
from app.rules.kernel import register_system

EXAMPLE = Path(__file__).resolve().parents[2] / "docs/plugins/wound_penalty.py"

GOOD = '''
from app.rules.config import RuleConfig, rule_field
from app.rules.kernel import Component
PLUGIN_API_VERSION = 1
RULE_SYSTEM = "coc"
NAME = "{name}"
class C(RuleConfig):
    {key}: bool = rule_field(False, title="开关")
COMPONENTS = [Component(id="{cid}", title="{name}", config=C, toggle="{key}", group="判定")]
'''


@pytest.fixture(autouse=True)
def restore_official():
    original = get_system("coc")
    yield
    register_system(original, replace=True)
    plugins._INFOS.clear()
    plugins._OFFICIAL.clear()


def _write(d: Path, name: str, text: str) -> None:
    (d / name).write_text(text, encoding="utf-8")


def _by_file(infos):
    return {i.file: i for i in infos["coc"]}


def test_example_plugin_loads_and_stays_off_until_enabled(tmp_path):
    shutil.copy(EXAMPLE, tmp_path / "wound_penalty.py")
    info = _by_file(plugins.install_all(tmp_path))["wound_penalty.py"]
    assert info.status == "loaded" and info.components == ["example.wound-penalty"]

    manifest = get_system("coc").manifest()
    comp = next(c for c in manifest["components"] if c["id"] == "example.wound-penalty")
    assert comp["source"] == "plugin:wound_penalty.py" and comp["toggle"] == "wound_penalty"

    hurt = {"skills": {"侦查": 50}, "system_data": {"hitPoints": {"current": 3, "max": 12}}}
    assert "example.wound-penalty" not in ruleset_for("coc").components
    off = ruleset_for("coc").check.resolve_skill_check(hurt, "侦查")
    on = ruleset_for("coc", {"wound_penalty": True}).check.resolve_skill_check(hurt, "侦查")
    assert off.penalty == 0
    assert on.penalty == 1 and on.modifiers[0]["reason"] == "伤势拖累了动作"
    assert "受伤减值" in "".join(ruleset_for("coc", {"wound_penalty": True}).labels())


def test_broken_plugins_are_isolated(tmp_path):
    _write(tmp_path, "a_good.py", GOOD.format(name="好插件", key="good_flag", cid="me.good"))
    _write(tmp_path, "b_syntax.py", "def oops(:\n")
    _write(tmp_path, "c_version.py", GOOD.replace("PLUGIN_API_VERSION = 1", "PLUGIN_API_VERSION = 99")
           .format(name="旧", key="old_flag", cid="me.old"))
    _write(tmp_path, "d_reserved.py", GOOD.format(name="冒名", key="fake_flag", cid="coc7.fake"))
    _write(tmp_path, "e_clash.py", GOOD.format(name="撞名", key="critical_max", cid="me.clash"))
    _write(tmp_path, "f_other.py", GOOD.replace('RULE_SYSTEM = "coc"', 'RULE_SYSTEM = "dnd"')
           .format(name="别家", key="x_flag", cid="me.dnd"))
    _write(tmp_path, "_ignored.py", "raise SystemExit")

    infos = _by_file(plugins.install_all(tmp_path))
    assert infos["a_good.py"].status == "loaded"
    assert "导入失败" in infos["b_syntax.py"].error
    assert "接口版本" in infos["c_version.py"].error
    assert "保留的前缀" in infos["d_reserved.py"].error
    assert infos["e_clash.py"].status == "failed" and "critical_max" in infos["e_clash.py"].error
    assert "f_other.py" not in infos and "_ignored.py" not in infos
    # 官方规则不受影响，好插件照常可用
    assert ruleset_for("coc", {"good_flag": True}).components[-1] == "me.good"
    assert ruleset_for("coc").check.resolve_skill_check({"skills": {"侦查": 50}}, "侦查")


def test_non_optional_plugin_component_rejected(tmp_path):
    _write(tmp_path, "always.py", GOOD.replace(', toggle="{key}"', "").format(
        name="常驻", key="always_flag", cid="me.always"))
    assert "必须是可选组件" in _by_file(plugins.install_all(tmp_path))["always.py"].error


def test_install_is_idempotent(tmp_path):
    shutil.copy(EXAMPLE, tmp_path / "wound_penalty.py")
    plugins.install_all(tmp_path)
    plugins.install_all(tmp_path)
    ids = [c.id for c in get_system("coc").components]
    assert ids.count("example.wound-penalty") == 1


def test_missing_directory_means_no_plugins(tmp_path):
    assert plugins.install_all(tmp_path / "nope") == {"coc": []}
    assert get_system("coc").id == "coc"
