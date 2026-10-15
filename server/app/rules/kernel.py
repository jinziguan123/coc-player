"""规则内核：把子规则组件组装成一份「生效规则」（Ruleset）。

设计见 ``docs/plans/2026-10-14-规则引擎组件化-design.md``。要点：

- **内核没有特权**：官方 CoC 7e 也只是「一组组件 + 一个预设」，内核只管组装与校验。
- **冲突即报错**：同一服务槽位只能有一个提供者，同一村规键只能有一个所有者，依赖缺失、
  互斥组件同时启用——这些都在注册规则系统时（即进程启动时）抛错，不留到某次结算才暴露。
- **村规自动注入**：组件把导出分成 ``bound``（结算要读村规）与 ``plain`` 两类。经
  Ruleset 取到的 ``bound`` 函数已绑好本局村规，调用方**不许**再传 ``options``——
  「忘了传村规」这类漂移（P0 修过的那几处）从结构上就不会再发生。
- **读时查找**：服务对象每次取属性都现查目标模块，不在构造时拷贝函数引用。这样测试对
  规则模块打的桩（monkeypatch）照样生效，缓存的 Ruleset 也不会握着过期的函数。

- **参数归组件**：每个组件用 :class:`~app.rules.config.RuleConfig` 声明自己的村规参数；
  规则系统把它们合成一个完整的村规对象（``options_model``）。可选组件的「开关」就是它
  自己的一个布尔参数（``toggle``），所以存档里只有参数，没有第二套「组件启用表」。

生效规则不可变、按（规则系统, 村规）缓存：换一份村规就是换一个对象，不存在「卸载」。
"""

from __future__ import annotations

import importlib
import logging
from collections import OrderedDict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.rules import data as rule_data
from app.rules import hooks as rule_hooks
from app.rules.config import RuleConfig, parse_lenient, parse_strict
from app.rules.data import DataTable

logger = logging.getLogger(__name__)

#: 房规文件格式版本（见设计文档 6.3）。升级时在 house_rules 里补一个迁移函数。
#: v2（P3）：新增 ``data`` 段——数据表补丁。
HOUSE_RULES_FORMAT_VERSION = 2


class RulesetError(ValueError):
    """规则系统组装失败（依赖缺失、冲突、槽位或村规键重复归属）。"""


@dataclass(frozen=True)
class Provide:
    """组件向自己的服务槽位提供的导出。

    ``target`` 是实现所在的模块：可以是模块对象，也可以是模块路径字符串——组件文件在
    末尾声明 ``Provide(__name__, ...)`` 指向自身，组装时再解析。``bound`` 里的名字调用时
    自动注入 ``options=<本局村规>``；``plain`` 里的名字原样透出（数据表、与村规无关的函数）。
    """

    target: Any
    bound: tuple[str, ...] = ()
    plain: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        dup = set(self.bound) & set(self.plain)
        if dup:
            raise RulesetError(f"导出名同时出现在 bound 与 plain：{sorted(dup)}")
        if not isinstance(self.target, str):
            self.check_exports()

    @property
    def obj(self) -> Any:
        if isinstance(self.target, str):
            return importlib.import_module(self.target)
        return self.target

    def check_exports(self) -> None:
        obj = self.obj
        missing = [n for n in (*self.bound, *self.plain) if not hasattr(obj, n)]
        if missing:
            raise RulesetError(f"{_target_name(obj)} 没有这些导出：{missing}")


@dataclass(frozen=True)
class Component:
    """一块子规则。只声明「提供什么、依赖谁、和谁互斥、有哪些参数」，不含状态。"""

    id: str
    title: str
    provides: dict[str, Provide] = field(default_factory=dict)
    requires: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    #: 本组件的村规参数。每个参数必须恰好归一个组件。
    config: type[RuleConfig] | None = None
    #: 可选组件的开关参数名（``config`` 里的一个 bool 字段）；None = 常驻组件。
    toggle: str | None = None
    #: 规则页上的分区（检定 / 幸运 / 伤害与状态 / 理智与疯狂 / 成长……）；空串 = 不在规则页出现。
    #: 分区按组件在预设里首次出现的顺序排列；插件可以并入已有分区，也可以开新分区。
    group: str = ""
    #: 可选规则卡上的来源标签：「官方可选」「本桌自定」「官方变体」等（插件一律显示「插件」）。
    tag: str = ""
    #: 给房主看的一句说明（可选组件的开关行下方显示它）。
    description: str = ""
    #: 本组件的数据表（房主/模组作者可以补丁改动的那些表）。表名在规则系统内唯一。
    data: tuple[DataTable, ...] = ()
    #: 本组件的钩子实现所在的模块（模块对象或路径，通常写 ``__name__``）。见 app.rules.hooks。
    hooks: Any = None
    #: 组件来源：``official`` 或 ``plugin:<插件名>``（第三方插件加载时填）。
    source: str = "official"

    @property
    def optional(self) -> bool:
        return self.toggle is not None

    def hooks_module(self) -> Any:
        if isinstance(self.hooks, str):
            return importlib.import_module(self.hooks)
        return self.hooks

    @property
    def option_keys(self) -> tuple[str, ...]:
        return tuple(self.config.model_fields) if self.config else ()

    def is_enabled(self, options: Any) -> bool:
        return self.toggle is None or bool(getattr(options, self.toggle))


@dataclass(frozen=True, eq=False)  # 按身份哈希：作 lru_cache 的键
class RuleSystem:
    """一套规则系统：组件目录 + 官方预设 + 合成后的村规模型。"""

    id: str
    title: str
    #: 官方预设的版本：房规文件记下它（``base_preset``），预设调整默认值时据此提示。
    version: int
    components: tuple[Component, ...]
    #: 官方预设包含的组件 id，顺序即 KP 描述、摘要标签、规则页的顺序。
    preset: tuple[str, ...]
    #: 各组件参数的合成（多继承各组件的 config）；字段全集须与组件参数一一对应。
    options_model: type[RuleConfig]
    _slots: dict[str, tuple[str, Provide]] = field(default_factory=dict, repr=False)
    _active: tuple[Component, ...] = field(default=(), repr=False)
    _default: Any = field(default=None, repr=False)
    _tables: dict[str, tuple[str, DataTable]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_default", self.options_model())
        active, slots, tables = _resolve(self)
        object.__setattr__(self, "_active", active)
        object.__setattr__(self, "_slots", slots)
        object.__setattr__(self, "_tables", tables)

    @property
    def active_components(self) -> tuple[Component, ...]:
        """预设内的组件，按预设顺序。"""
        return self._active

    # ── 数据表 ──

    @property
    def data_tables(self) -> tuple[DataTable, ...]:
        """预设内所有组件的数据表，按预设顺序。"""
        return tuple(t for _, t in self._tables.values())

    def data_table(self, name: str) -> DataTable:
        try:
            return self._tables[name][1]
        except KeyError:
            raise ValueError(f"规则系统 {self.id} 没有数据表 {name}") from None

    def resolve_rows(self, layers: Sequence[Mapping | None] = ()) -> dict[str, tuple[dict, ...]]:
        """官方数据依次叠上各层补丁（如 模组 → 村规），返回 表名 → 合并后的行。"""
        out: dict[str, tuple[dict, ...]] = {}
        for name, (_cid, table) in self._tables.items():
            rows = table.official_rows
            for layer in layers:
                if layer and layer.get(name):
                    rows = rule_data.apply_patches(table, rows, layer[name])
            out[name] = rows
        return out

    # ── 村规 ──

    @property
    def default_options(self) -> Any:
        return self._default

    def parse_options(self, raw: dict | None) -> Any:
        """宽松解析（落库/界面提交）：未知键丢弃、值钳进合法区间。"""
        return parse_lenient(self.options_model, raw, self._default)

    def parse_options_strict(self, raw: Any) -> tuple[dict, list[str], list[str]]:
        """严格解析（导入房规文件）：返回 (差异项, 错误, 未知键)。"""
        return parse_strict(self.options_model, raw)

    @staticmethod
    def merge_options(*layers: dict | None) -> dict:
        """按「后者覆盖前者」合并村规层（模组默认 → 村规 → 本局覆盖）。

        不做 Foundry 那种 mode/priority——村规是标量参数，不存在「多来源叠加同一个值」，
        引进优先级只会让「我改的这一项到底生效没有」变得不可解释。
        """
        merged: dict = {}
        for layer in layers:
            for key, value in (layer or {}).items():
                if value is not None:
                    merged[key] = value
        return merged

    def describe_options(self, options: Any) -> list[str]:
        """与 RAW 不同之处的人话（给 KP），按预设顺序逐组件拼接。"""
        return [line for c in self._active if c.config for line in c.config.describe(options)]

    def enabled_components(self, options: Any) -> tuple[tuple[Component, ...], list[str]]:
        """按村规开关算出本局实际启用的组件，并报出开关组合的问题（依赖没开、互斥都开）。

        有问题的组件按「后者让位」剔除：依赖缺失的不启用；互斥的两个只保留预设里靠前的那个。
        """
        errors: list[str] = []
        on = [c for c in self._active if c.is_enabled(options)]
        changed = True
        while changed:   # 依赖可能是另一个可选组件：剔除一个可能连带剔除下一个
            changed = False
            ids = {c.id for c in on}
            for comp in list(on):
                missing = [d for d in comp.requires if d not in ids]
                if missing:
                    titles = "、".join(self._title(d) for d in missing)
                    errors.append(f"「{comp.title}」需要先开启「{titles}」")
                    on.remove(comp)
                    changed = True
        kept: list[Component] = []
        for comp in on:
            clash = [k for k in kept if k.id in comp.conflicts or comp.id in k.conflicts]
            if clash:
                errors.append(f"「{comp.title}」与「{clash[0].title}」不能同时开启")
                continue
            kept.append(comp)
        return tuple(kept), errors

    def check_options(self, options: dict | None) -> list[str]:
        """保存村规前的校验：开关组合是否成立（依赖、互斥）。返回可读的问题列表。"""
        return self.enabled_components(self.parse_options(options))[1]

    def _title(self, component_id: str) -> str:
        for c in self.components:
            if c.id == component_id:
                return c.title
        return component_id

    def option_labels(self, options: Any) -> list[str]:
        """与 RAW 不同之处的短标签（给玩家看的规矩摘要）。"""
        return [text for c in self._active if c.config for text in c.config.labels(options)]

    # ── 组件 ──

    def manifest(self) -> dict:
        """前端规则页、数据表编辑器与房规文件的契约：组件目录、各组件参数与数据表的 schema。"""
        return {
            "id": self.id,
            "title": self.title,
            "version": self.version,
            "house_rules_format_version": HOUSE_RULES_FORMAT_VERSION,
            "tables": [
                {
                    "name": t.name, "title": t.title, "description": t.description,
                    "key": t.key, "component": cid, "row_schema": t.schema(),
                }
                for cid, t in self._tables.values()
            ],
            "components": [
                {
                    "id": c.id,
                    "title": c.title,
                    "description": c.description,
                    "group": c.group,
                    "toggle": c.toggle,
                    "tag": c.tag,
                    "source": c.source,
                    "requires": list(c.requires),
                    "conflicts": list(c.conflicts),
                    "config_schema": c.config.model_json_schema() if c.config else None,
                }
                for c in self._active
            ],
        }

    def ruleset(
        self, options: dict | None = None, data_layers: Sequence[Mapping | None] = (),
    ) -> Ruleset:
        """按村规参数与数据补丁层取生效规则（内容相同则返回同一个缓存对象）。

        ``data_layers`` 自底向上（如 [模组补丁, 村规补丁]），每层是 表名 → 补丁行列表。
        """
        return _build(self, self.parse_options(options), tuple(layer or {} for layer in data_layers))


class Service:
    """某个服务槽位在一份生效规则下的视图：``bound`` 导出自动带上本局村规。"""

    __slots__ = ("_slot", "_provide", "_options")

    def __init__(self, slot: str, provide: Provide, options: Any) -> None:
        self._slot = slot
        self._provide = provide
        self._options = options

    def __getattr__(self, name: str) -> Any:
        p = self._provide
        if name in p.plain:
            return getattr(p.obj, name)
        if name in p.bound:
            fn = getattr(p.obj, name)  # 读时查找：monkeypatch 与热替换都能看到
            options = self._options

            def call(*args, **kwargs):
                if "options" in kwargs:
                    raise TypeError(
                        f"{self._slot}.{name}：村规由生效规则自动注入，调用方不要传 options",
                    )
                return fn(*args, options=options, **kwargs)

            call.__name__ = name
            call.__doc__ = getattr(fn, "__doc__", None)
            return call
        raise AttributeError(f"规则服务 {self._slot!r} 没有导出 {name!r}")

    def __repr__(self) -> str:
        return f"<Service {self._slot} → {_target_name(self._provide.obj)}>"


class Ruleset:
    """一份生效规则：``rs.<槽位>.<导出>``，例如 ``rs.check.resolve_skill_check(...)``。"""

    __slots__ = ("system", "options", "components", "_services", "_rows", "_tables")

    def __init__(self, system: RuleSystem, options: Any, rows: dict[str, tuple[dict, ...]]) -> None:
        self.system = system.id
        self._rows = rows
        self._tables = {
            name: table.runtime(rows[name]) for name, (_cid, table) in system._tables.items()
        }
        enabled, problems = system.enabled_components(options)
        for p in problems:   # 保存时已校验过；走到这里多半是旧存档或手改数据库，降级而不是崩
            logger.warning("规则系统 %s 的组件开关组合有问题，已按后者让位处理：%s", system.id, p)
        #: 本份村规下实际启用的组件（可选组件随其开关参数进出）。
        self.components = tuple(c.id for c in enabled)
        # 本局的钩子：只登记启用的组件
        pm = rule_hooks.new_manager()
        for comp in enabled:
            if comp.hooks is not None:
                pm.register(comp.hooks_module(), name=comp.id)
        # 「本局规则」= 参数 + 数据表 + 钩子：附在注入给组件函数的那个对象上
        ctx = options.model_copy()
        ctx._tables = self._tables
        ctx._hooks = pm.hook
        self.options = ctx
        self._services = {
            slot: Service(slot, provide, ctx)
            for slot, (cid, provide) in system._slots.items()
            if cid in self.components
        }

    def rows(self, table: str) -> tuple[dict, ...]:
        """某张数据表本局合并后的行（官方 + 各层补丁），供接口与编辑器展示。"""
        return self._rows[table]

    def __getattr__(self, slot: str) -> Service:
        try:
            return self._services[slot]
        except KeyError:
            raise AttributeError(
                f"规则系统 {self.system!r} 没有服务槽位 {slot!r}"
                f"（已有：{', '.join(sorted(self._services))}）",
            ) from None

    def has(self, slot: str) -> bool:
        """这套规则系统有没有某个服务槽位（如某些规则系统根本没有「成长」）。"""
        return slot in self._services

    def describe(self) -> list[str]:
        """本份村规与 RAW 不同之处的人话描述（供注入 KP 与规划器）。"""
        return _SYSTEMS[self.system].describe_options(self.options)

    def data_changes(self) -> list[tuple[str, list[tuple[str, str]]]]:
        """本局数据表相对官方数据改了什么（供注入 KP）：[(表名, [(全文, 短文), ...])]，只列有改动的表。"""
        out: list[tuple[str, list[tuple[str, str]]]] = []
        for table in _SYSTEMS[self.system].data_tables:
            items = rule_data.changes(table, table.official_rows, self._rows[table.name])
            if items:
                out.append((table.title, items))
        return out

    def labels(self) -> list[str]:
        """本份村规与 RAW 不同之处的短标签（给玩家看）。"""
        return _SYSTEMS[self.system].option_labels(self.options)

    def __repr__(self) -> str:
        return f"<Ruleset {self.system} components={len(self.components)}>"


# ── 注册表 ────────────────────────────────────────────────────────────────

_SYSTEMS: dict[str, RuleSystem] = {}


def register_system(system: RuleSystem, *, replace: bool = False) -> None:
    """注册规则系统。``replace=True`` 用于叠上第三方插件后替换官方版本（见 app.rules.plugins）。"""
    if system.id in _SYSTEMS and _SYSTEMS[system.id] is not system and not replace:
        raise RulesetError(f"规则系统 {system.id!r} 重复注册")
    _SYSTEMS[system.id] = system


def get_system(rule_system: str) -> RuleSystem:
    try:
        return _SYSTEMS[rule_system]
    except KeyError:
        raise ValueError(f"未注册的规则系统: {rule_system}") from None


def list_systems() -> list[str]:
    return list(_SYSTEMS)


def ruleset_for(rule_system: str, options: dict | None = None) -> Ruleset:
    """无会话上下文时（建卡、规则资料接口）的取法：``options=None`` 即 RAW。"""
    return get_system(rule_system).ruleset(options)


# ── 内部 ──────────────────────────────────────────────────────────────────


_CACHE: OrderedDict[tuple, Ruleset] = OrderedDict()
_CACHE_MAX = 256


def _build(system: RuleSystem, options: Any, layers: tuple[Mapping, ...]) -> Ruleset:
    """按（规则系统, 参数, 数据补丁内容）缓存生效规则；最久未用的先淘汰。"""
    data_key = rule_data.digest(layers) if any(layers) else ""
    key = (id(system), options, data_key)
    hit = _CACHE.get(key)
    if hit is not None:
        _CACHE.move_to_end(key)
        return hit
    rs = Ruleset(system, options, system.resolve_rows(layers))
    _CACHE[key] = rs
    if len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)
    return rs


def _resolve(system: RuleSystem) -> tuple[
    tuple[Component, ...], dict[str, tuple[str, Provide]], dict[str, tuple[str, DataTable]],
]:
    """校验预设并算出（预设内组件, 槽位 → 导出, 表名 → 数据表）。任何问题都在这里一次性报全。"""
    catalog: dict[str, Component] = {}
    errors: list[str] = []
    for comp in system.components:
        if comp.id in catalog:
            errors.append(f"组件 id 重复：{comp.id}")
        catalog[comp.id] = comp

    enabled = list(dict.fromkeys(system.preset))
    errors += [f"预设引用了不存在的组件：{cid}" for cid in enabled if cid not in catalog]
    active = [catalog[cid] for cid in enabled if cid in catalog]
    active_ids = {c.id for c in active}

    for comp in active:
        for dep in comp.requires:
            if dep not in active_ids:
                errors.append(f"组件 {comp.id} 依赖 {dep}，但它没有启用")
        for other in comp.conflicts:
            # 两个都是常驻组件才是组装错误；有可选组件参与的互斥按村规开关在运行时判
            other_comp = catalog.get(other)
            if other in active_ids and not comp.optional and other_comp and not other_comp.optional:
                errors.append(f"组件 {comp.id} 与 {other} 互斥，不能同时启用")

    slots: dict[str, tuple[str, Provide]] = {}
    for comp in active:
        for slot, provide in comp.provides.items():
            try:
                provide.check_exports()
            except RulesetError as e:
                errors.append(f"组件 {comp.id}：{e}")
            if slot in slots:
                errors.append(f"服务槽位 {slot} 同时由 {slots[slot][0]} 与 {comp.id} 提供")
            else:
                slots[slot] = (comp.id, provide)

    tables: dict[str, tuple[str, DataTable]] = {}
    for comp in active:
        for table in comp.data:
            if table.name in tables:
                errors.append(f"数据表 {table.name} 同时由 {tables[table.name][0]} 与 {comp.id} 声明")
                continue
            try:
                table.official_rows  # 官方数据在启动时就校验，坏数据不留到结算才暴露
            except Exception as e:  # noqa: BLE001
                errors.append(f"组件 {comp.id} 的官方数据 {table.name} 无效：{e}")
                continue
            tables[table.name] = (comp.id, table)

    errors += _check_option_owners(active, tuple(system.options_model.model_fields))
    errors += _check_toggles(active)
    errors += _check_hooks(active)
    if errors:
        raise RulesetError(f"规则系统 {system.id} 组装失败：\n- " + "\n- ".join(errors))
    return tuple(active), slots, tables


def _check_option_owners(active: Iterable[Component], option_fields: tuple[str, ...]) -> list[str]:
    owner: dict[str, str] = {}
    errors: list[str] = []
    known = set(option_fields)
    for comp in active:
        for key in comp.option_keys:
            if key not in known:
                errors.append(f"组件 {comp.id} 声明了未知的村规键 {key}")
            elif key in owner:
                errors.append(f"村规键 {key} 同时归 {owner[key]} 与 {comp.id} 所有")
            else:
                owner[key] = comp.id
    errors += [f"村规键 {key} 没有任何组件认领" for key in option_fields if key not in owner]
    return errors


def _check_hooks(active: Iterable[Component]) -> list[str]:
    """每个组件的钩子实现都要对得上某个钩子声明——名字拼错在启动时就报。"""
    errors: list[str] = []
    for comp in active:
        if comp.hooks is None:
            continue
        pm = rule_hooks.new_manager()
        try:
            pm.register(comp.hooks_module(), name=comp.id)
            pm.check_pending()
        except Exception as e:  # noqa: BLE001
            errors.append(f"组件 {comp.id} 的钩子无效：{e}")
    return errors


def _check_toggles(active: Iterable[Component]) -> list[str]:
    errors: list[str] = []
    for comp in active:
        if comp.toggle is None:
            continue
        if comp.toggle not in comp.option_keys:
            errors.append(f"组件 {comp.id} 的开关 {comp.toggle} 不是它自己的参数")
        elif comp.config.model_fields[comp.toggle].annotation is not bool:
            errors.append(f"组件 {comp.id} 的开关 {comp.toggle} 必须是布尔参数")
    return errors


def _target_name(target: Any) -> str:
    return getattr(target, "__name__", None) or type(target).__name__
