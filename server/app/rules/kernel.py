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

生效规则不可变、按（规则系统, 村规）缓存：换一份村规就是换一个对象，不存在「卸载」。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any


class RulesetError(ValueError):
    """规则系统组装失败（依赖缺失、冲突、槽位或村规键重复归属）。"""


@dataclass(frozen=True)
class Provide:
    """组件向某个服务槽位提供的导出。

    ``target`` 是实现所在的模块（或对象）；``bound`` 里的名字调用时自动注入
    ``options=<本局村规>``，``plain`` 里的名字原样透出（纯数据表、与村规无关的函数）。
    """

    target: Any
    bound: tuple[str, ...] = ()
    plain: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        dup = set(self.bound) & set(self.plain)
        if dup:
            raise RulesetError(f"导出名同时出现在 bound 与 plain：{sorted(dup)}")
        missing = [n for n in (*self.bound, *self.plain) if not hasattr(self.target, n)]
        if missing:
            raise RulesetError(f"{_target_name(self.target)} 没有这些导出：{missing}")


@dataclass(frozen=True)
class Component:
    """一块子规则。只声明「提供什么、依赖谁、和谁互斥、管哪些村规键」，不含状态。"""

    id: str
    title: str
    provides: dict[str, Provide]
    requires: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    #: 本组件读取的村规键。每个键必须恰好有一个所有者（P2 起由组件自己的 schema 定义）。
    option_keys: tuple[str, ...] = ()


@dataclass(frozen=True, eq=False)  # 按身份哈希：作 lru_cache 的键
class RuleSystem:
    """一套规则系统：组件目录 + 官方预设 + 村规的解析/合并/描述。"""

    id: str
    components: tuple[Component, ...]
    #: 官方预设启用的组件 id（P1 只有预设，房主开关组件见 P2）。
    preset: tuple[str, ...]
    #: 村规键全集；用来校验「每个键恰好一个所有者」。
    option_fields: tuple[str, ...]
    #: dict（可为 None）→ 规范化、可哈希的村规对象（未知键丢弃、值钳进合法区间）。
    parse_options: Callable[[dict | None], Any]
    #: 多层村规按「后者覆盖前者」合并成一个 dict。
    merge_options: Callable[..., dict]
    #: 村规对象 → 与 RAW 不同的那几项的人话描述（注入 KP）。
    #: 村规对象本身还须提供 ``to_dict()``（完整值）与 ``diff_from_default()``（差异项）。
    describe_options: Callable[[Any], list[str]]
    _slots: dict[str, tuple[str, Provide]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_slots", _resolve(self))

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
            return getattr(p.target, name)
        if name in p.bound:
            fn = getattr(p.target, name)  # 读时查找：monkeypatch 与热替换都能看到
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
        return f"<Service {self._slot} → {_target_name(self._provide.target)}>"


class Ruleset:
    """一份生效规则：``rs.<槽位>.<导出>``，例如 ``rs.check.resolve_skill_check(...)``。"""

    __slots__ = ("system", "options", "components", "_services")

    def __init__(self, system: RuleSystem, options: Any) -> None:
        self.system = system.id
        self.options = options
        self.components = system.preset
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

    def describe(self) -> list[str]:
        """本份村规与 RAW 不同之处的人话描述（供注入 KP 与规划器）。"""
        return _SYSTEMS[self.system].describe_options(self.options)

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


def _resolve(system: RuleSystem) -> dict[str, tuple[str, Provide]]:
    """校验预设并算出「槽位 → (组件 id, 导出)」。任何问题都在这里一次性报全。"""
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
            if slot in slots:
                errors.append(f"服务槽位 {slot} 同时由 {slots[slot][0]} 与 {comp.id} 提供")
            else:
                slots[slot] = (comp.id, provide)

    errors += _check_option_owners(active, system.option_fields)
    if errors:
        raise RulesetError(f"规则系统 {system.id} 组装失败：\n- " + "\n- ".join(errors))
    return slots


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


def _target_name(target: Any) -> str:
    return getattr(target, "__name__", None) or type(target).__name__
