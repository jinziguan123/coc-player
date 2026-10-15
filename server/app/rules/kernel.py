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
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from app.rules.config import RuleConfig, parse_lenient, parse_strict

#: 房规文件格式版本（见设计文档 6.3）。升级时在 house_rules 里补一个迁移函数。
HOUSE_RULES_FORMAT_VERSION = 1


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
    #: 村规面板上的分栏（如「判定」「伤害与状态」）；空串 = 不在面板上出现。
    group: str = ""
    #: 给房主看的一句说明（可选组件的开关行下方显示它）。
    description: str = ""

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
    #: 官方预设包含的组件 id，顺序即 KP 描述、摘要标签、村规面板的顺序。
    preset: tuple[str, ...]
    #: 各组件参数的合成（多继承各组件的 config）；字段全集须与组件参数一一对应。
    options_model: type[RuleConfig]
    _slots: dict[str, tuple[str, Provide]] = field(default_factory=dict, repr=False)
    _active: tuple[Component, ...] = field(default=(), repr=False)
    _default: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_default", self.options_model())
        active, slots = _resolve(self)
        object.__setattr__(self, "_active", active)
        object.__setattr__(self, "_slots", slots)

    @property
    def active_components(self) -> tuple[Component, ...]:
        """预设内的组件，按预设顺序。"""
        return self._active

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

    def option_labels(self, options: Any) -> list[str]:
        """与 RAW 不同之处的短标签（给玩家看的规矩摘要）。"""
        return [text for c in self._active if c.config for text in c.config.labels(options)]

    # ── 组件 ──

    def manifest(self) -> dict:
        """前端村规面板与房规文件的契约：组件目录 + 各组件参数的 JSON Schema。"""
        return {
            "id": self.id,
            "title": self.title,
            "version": self.version,
            "house_rules_format_version": HOUSE_RULES_FORMAT_VERSION,
            "components": [
                {
                    "id": c.id,
                    "title": c.title,
                    "description": c.description,
                    "group": c.group,
                    "toggle": c.toggle,
                    "requires": list(c.requires),
                    "conflicts": list(c.conflicts),
                    "config_schema": c.config.model_json_schema() if c.config else None,
                }
                for c in self._active
            ],
        }

    def ruleset(self, options: dict | None = None) -> Ruleset:
        """按村规取生效规则（同一份村规返回同一个缓存对象）。"""
        return _build(self, self.parse_options(options))


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

    __slots__ = ("system", "options", "components", "_services")

    def __init__(self, system: RuleSystem, options: Any) -> None:
        self.system = system.id
        self.options = options
        #: 本份村规下实际启用的组件（可选组件随其开关参数进出）。
        self.components = tuple(c.id for c in system._active if c.is_enabled(options))
        self._services = {
            slot: Service(slot, provide, options)
            for slot, (_cid, provide) in system._slots.items()
        }

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

    def labels(self) -> list[str]:
        """本份村规与 RAW 不同之处的短标签（给玩家看）。"""
        return _SYSTEMS[self.system].option_labels(self.options)

    def __repr__(self) -> str:
        return f"<Ruleset {self.system} components={len(self.components)}>"


# ── 注册表 ────────────────────────────────────────────────────────────────

_SYSTEMS: dict[str, RuleSystem] = {}


def register_system(system: RuleSystem) -> None:
    if system.id in _SYSTEMS and _SYSTEMS[system.id] is not system:
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


@lru_cache(maxsize=256)
def _build(system: RuleSystem, options: Any) -> Ruleset:
    return Ruleset(system, options)


def _resolve(system: RuleSystem) -> tuple[tuple[Component, ...], dict[str, tuple[str, Provide]]]:
    """校验预设并算出（预设内组件, 槽位 → (组件 id, 导出)）。任何问题都在这里一次性报全。"""
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
            if other in active_ids:
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

    errors += _check_option_owners(active, tuple(system.options_model.model_fields))
    errors += _check_toggles(active)
    if errors:
        raise RulesetError(f"规则系统 {system.id} 组装失败：\n- " + "\n- ".join(errors))
    return tuple(active), slots


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
