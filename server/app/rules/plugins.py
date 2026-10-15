"""第三方规则插件：从本机 ``plugins/`` 目录加载组件，叠进规则系统。

**安全边界**：插件是 Python 代码，以本应用的权限在本机运行，不做沙箱——性质同游戏 mod，
由装它的人自担风险（本项目开源发布、用户在自己机器上运行，见 ADR-001）。界面上会写明这一点。
与之相对，**房规文件**只有数据、绝不执行代码；别人发来的规则改动，能用房规文件表达的就别做成插件。

一个插件 = ``plugins/`` 下的一个 ``.py`` 文件或带 ``__init__.py`` 的目录，模块里声明：

.. code-block:: python

    PLUGIN_API_VERSION = 1          # 对应 :data:`PLUGIN_API_VERSION`，不一致则不加载
    RULE_SYSTEM = "coc"
    NAME = "受伤减值"
    VERSION = "1.0"
    DESCRIPTION = "HP 低于一半时所有检定多一颗惩罚骰"
    COMPONENTS = [Component(...)]    # 写法同官方组件，见 app/rules/coc/components/

插件组件的硬约束：

- 必须是**可选组件**（有 ``toggle``）：装上不等于启用，房主在规则页里打开才生效；
- id 不得以 ``coc7.`` 开头（官方保留），且不得与已有组件、服务槽位、村规参数、数据表重名；
- 可以声明村规参数（``config``）、数据表（``data``）、钩子（``hooks``）与服务槽位。

**失败隔离**：每个插件单独校验——导入报错、版本不符、与官方或其他插件冲突，都只让这一个
插件不加载并记下原因（规则书页可见），不影响官方规则与其他插件，更不会让应用起不来。
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

from pydantic import create_model

from app.rules.kernel import Component, RuleSystem, RulesetError, get_system, list_systems, register_system

logger = logging.getLogger(__name__)

#: 插件接口版本。组件/钩子/数据表的写法有不兼容的变化时加一，旧插件会被拒绝并提示升级。
PLUGIN_API_VERSION = 1
_RESERVED_PREFIX = "coc7."
_MODULE_NS = "coc_player_plugins"


@dataclass
class PluginInfo:
    """一个插件的加载结果（供规则书页展示）。"""

    file: str
    name: str = ""
    version: str = ""
    description: str = ""
    rule_system: str = ""
    status: str = "failed"   # loaded | failed
    error: str = ""
    components: list[str] = field(default_factory=list)


def discover(directory: Path) -> list[Path]:
    """目录下的插件入口：``*.py`` 文件与带 ``__init__.py`` 的子目录（下划线开头的忽略）。"""
    if not directory.is_dir():
        return []
    out: list[Path] = []
    for p in sorted(directory.iterdir()):
        if p.name.startswith(("_", ".")):
            continue
        if p.is_file() and p.suffix == ".py":
            out.append(p)
        elif p.is_dir() and (p / "__init__.py").is_file():
            out.append(p / "__init__.py")
    return out


def load_module(path: Path) -> ModuleType:
    stem = path.parent.name if path.name == "__init__.py" else path.stem
    name = f"{_MODULE_NS}.{stem}"
    spec = importlib.util.spec_from_file_location(
        name, path, submodule_search_locations=[str(path.parent)] if path.name == "__init__.py" else None,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


def read_plugin(path: Path, rule_system: str) -> tuple[PluginInfo, list[Component]]:
    """导入一个插件并做静态校验（不涉及与其他组件的冲突）。"""
    info = PluginInfo(file=path.parent.name if path.name == "__init__.py" else path.name)
    try:
        module = load_module(path)
    except Exception as e:  # noqa: BLE001 — 插件里什么错都可能有，一律隔离
        info.error = f"导入失败：{type(e).__name__}: {e}"
        return info, []

    info.name = str(getattr(module, "NAME", "") or info.file)
    info.version = str(getattr(module, "VERSION", "") or "")
    info.description = str(getattr(module, "DESCRIPTION", "") or "")
    info.rule_system = str(getattr(module, "RULE_SYSTEM", "") or "")

    api = getattr(module, "PLUGIN_API_VERSION", None)
    if api != PLUGIN_API_VERSION:
        info.error = f"插件接口版本是 {api}，本应用需要 {PLUGIN_API_VERSION}，请换用匹配的插件版本"
        return info, []
    if info.rule_system != rule_system:
        info.status = "skipped"
        info.error = f"这是给「{info.rule_system or '未声明'}」规则系统的插件"
        return info, []

    comps = getattr(module, "COMPONENTS", None)
    if not isinstance(comps, (list, tuple)) or not comps or not all(isinstance(c, Component) for c in comps):
        info.error = "COMPONENTS 应为非空的 Component 列表"
        return info, []
    problems: list[str] = []
    for c in comps:
        if c.id.startswith(_RESERVED_PREFIX):
            problems.append(f"组件 id「{c.id}」用了官方保留的前缀 {_RESERVED_PREFIX}")
        if not c.optional:
            problems.append(f"组件「{c.id}」必须是可选组件（声明 toggle），装上后由房主决定是否启用")
    if problems:
        info.error = "；".join(problems)
        return info, []
    source = f"plugin:{info.file}"
    components = [_with_source(c, source) for c in comps]
    info.components = [c.id for c in components]
    return info, components


def assemble(base: RuleSystem, plugins: list[tuple[PluginInfo, list[Component]]]) -> RuleSystem:
    """把通过静态校验的插件逐个叠进规则系统；冲突的插件剔除并记下原因。返回新的规则系统。"""
    system = base
    for info, comps in plugins:
        if not comps:
            continue
        try:
            system = _extend(system, comps)
        except RulesetError as e:
            info.status = "failed"
            info.error = str(e).split("\n", 1)[-1].replace("\n- ", "；").lstrip("- ")
            continue
        info.status = "loaded"
        info.error = ""
    return system


def install(base: RuleSystem, directory: Path) -> tuple[RuleSystem, list[PluginInfo]]:
    """发现、校验、组装。返回（叠上插件后的规则系统, 每个插件的加载结果）。"""
    loaded = [read_plugin(p, base.id) for p in discover(directory)]
    system = assemble(base, loaded)
    infos = [info for info, _ in loaded]
    for info in infos:
        if info.status == "loaded":
            logger.info("已加载规则插件 %s %s（%s）", info.name, info.version, ", ".join(info.components))
        elif info.status == "failed":
            logger.warning("规则插件 %s 未加载：%s", info.file, info.error)
    return system, infos


#: 启动时注册的官方规则系统（叠插件总是从它出发，重复安装不会叠两次）。
_OFFICIAL: dict[str, RuleSystem] = {}
#: 最近一次安装的加载结果，按规则系统。
_INFOS: dict[str, list[PluginInfo]] = {}


def install_all(directory: Path) -> dict[str, list[PluginInfo]]:
    """应用启动时调用：给每个已注册的规则系统叠上 ``directory`` 里的插件，并替换注册。"""
    for sid in list_systems():
        base = _OFFICIAL.setdefault(sid, get_system(sid))
        system, infos = install(base, directory)
        register_system(system, replace=True)
        _INFOS[sid] = [i for i in infos if i.status != "skipped"]
    return dict(_INFOS)


def installed(rule_system: str) -> list[PluginInfo]:
    """某规则系统的插件加载结果（含加载失败的，供规则书页展示原因）。"""
    return list(_INFOS.get(rule_system, []))


# ── 内部 ─────────────────────────────────────────────────────────────────


def _with_source(c: Component, source: str) -> Component:
    from dataclasses import replace

    return replace(c, source=source)


def _extend(system: RuleSystem, extra: list[Component]) -> RuleSystem:
    """在规则系统上追加组件：村规模型多继承插件的参数，预设末尾追加插件组件。"""
    configs = tuple(c.config for c in extra if c.config is not None)
    options_model = system.options_model
    if configs:
        options_model = create_model(
            f"{system.options_model.__name__}Plus",
            __base__=(system.options_model, *configs),
        )
    return RuleSystem(
        id=system.id,
        title=system.title,
        version=system.version,
        components=(*system.components, *extra),
        preset=(*system.preset, *(c.id for c in extra)),
        options_model=options_model,
    )
