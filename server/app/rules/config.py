"""组件配置（村规参数）的公共底座：字段声明、宽松解析、严格解析。

每个组件把自己读的村规参数写成一个 :class:`RuleConfig` 子类，参数的**全部**元信息只在
这里写一次：默认值（＝规则书原文）、合法区间、可选项文案、界面说明、依附关系。前端规则页、
KP 提示词里的规则描述、玩家看的规矩摘要、房规文件的校验，都从这一份生成。

两种解析，对应两种来源：

- :func:`parse_lenient`：落库的、界面整份提交的配置。越界钳回区间、类型不对退回默认、
  未知键丢弃——房主填错一个数字不该让整局掷不了骰，旧版本存下的废键也不该把读取搞崩。
- :func:`parse_strict`：导入的房规文件。它是别人写的，房主要在导入前看清楚：越界与类型
  错误逐条报出（带字段路径），未知键单列成警告，什么都不悄悄修正。
"""

from __future__ import annotations

import typing
from typing import Any

import annotated_types
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr


class RuleConfig(BaseModel):
    """一个组件的村规参数。字段默认值 = 规则书原文（RAW）。

    子类可覆写 :meth:`describe`（给 KP：与 RAW 不同之处的人话）与 :meth:`labels`
    （给玩家：短标签）。两者都在**合成后的完整村规对象**上调用（``CheckConfig.describe(opts)``），
    所以只该读本组件自己的字段。
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    #: 本局合并后的数据表（表名 → 运行时形态）。由 Ruleset 在注入前附上；
    #: 不参与相等与哈希——缓存键另算（见 kernel._build）。
    _tables: Any = PrivateAttr(default=None)
    #: 本局的钩子（``pm.hook``），只含实际启用的组件；同样由 Ruleset 附上。见 app.rules.hooks。
    _hooks: Any = PrivateAttr(default=None)

    def table(self, name: str, official: Any) -> Any:
        """取本局的某张数据表；没附表（未经 Ruleset 注入，如单测直接调）时用官方数据。"""
        tables = self._tables
        return tables[name] if tables is not None and name in tables else official

    def describe(self) -> list[str]:
        return []

    def labels(self) -> list[str]:
        return []

    def to_dict(self) -> dict:
        return self.model_dump()

    def diff_from_default(self) -> dict:
        """只列出与 RAW 不同的项——落库、导出、展示都用它。"""
        default = type(self)()
        return {
            name: getattr(self, name)
            for name in type(self).model_fields
            if getattr(self, name) != getattr(default, name)
        }


def rule_field(
    default: Any,
    *,
    title: str,
    hint: str = "",
    ge: int | None = None,
    le: int | None = None,
    choices: dict[str, tuple[str, str]] | None = None,
    show_if: dict[str, Any] | None = None,
) -> Any:
    """声明一个村规参数。

    - ``hint``：界面上的说明；可含 ``{value}``，由前端代入当前值。
    - ``choices``：枚举项 → (短标签, 说明)。短标签放进下拉框，说明在选中时显示。
    - ``show_if``：仅当另一参数取某值时才显示（如「固定点数」只在口径选「固定点数」时出现）。
    """
    extra: dict[str, Any] = {}
    if choices:
        extra["x-choices"] = [
            {"value": value, "label": label, "hint": desc}
            for value, (label, desc) in choices.items()
        ]
    if show_if:
        extra["x-show-if"] = show_if
    return Field(
        default, title=title, description=hint or None, ge=ge, le=le,
        json_schema_extra=extra or None,
    )


# ── 解析 ──────────────────────────────────────────────────────────────────


def _bounds(model: type[RuleConfig], name: str) -> tuple[int | None, int | None]:
    low = high = None
    for meta in model.model_fields[name].metadata:
        if isinstance(meta, annotated_types.Ge):
            low = meta.ge
        elif isinstance(meta, annotated_types.Le):
            high = meta.le
    return low, high


def _choices(model: type[RuleConfig], name: str) -> tuple[str, ...] | None:
    annotation = model.model_fields[name].annotation
    if typing.get_origin(annotation) is typing.Literal:
        return tuple(typing.get_args(annotation))
    return None


def _kind(model: type[RuleConfig], name: str) -> str:
    if _choices(model, name) is not None:
        return "enum"
    annotation = model.model_fields[name].annotation
    return "bool" if annotation is bool else "int"


def table_of(options: RuleConfig | None, name: str, official: Any) -> Any:
    """组件算法取数据表的统一入口：``options`` 为 None（未传村规）时用官方数据。"""
    return official if options is None else options.table(name, official)


def parse_lenient(model: type[RuleConfig], raw: dict | None, default: RuleConfig) -> RuleConfig:
    """宽松解析（落库/界面提交）：钳区间、坏值退默认、未知键丢弃。全默认时返回 ``default`` 本身。"""
    if isinstance(raw, model):
        return raw
    if not raw:
        return default
    patch: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in model.model_fields:
            continue
        kind = _kind(model, key)
        if kind == "enum":
            text = str(value or "").strip()
            if text in (_choices(model, key) or ()):
                patch[key] = text
        elif kind == "int":
            try:
                number = int(value)
            except (TypeError, ValueError):
                continue
            low, high = _bounds(model, key)
            if low is not None:
                number = max(low, number)
            if high is not None:
                number = min(high, number)
            patch[key] = number
        else:
            patch[key] = bool(value)
    return default.model_copy(update=patch) if patch else default


def parse_strict(model: type[RuleConfig], raw: Any) -> tuple[dict, list[str], list[str]]:
    """严格解析（导入房规文件）。返回 (只含差异项的 dict, 错误, 未知键)。

    错误不为空时调用方不该落库；未知键（本机没有的组件的参数、或拼错的名字）只是警告，
    导入时忽略。
    """
    if raw is None:
        return {}, [], []
    if not isinstance(raw, dict):
        return {}, ["options 必须是「参数名: 取值」的映射"], []
    errors: list[str] = []
    unknown: list[str] = []
    accepted: dict[str, Any] = {}
    for key, value in raw.items():
        key = str(key)
        if key not in model.model_fields:
            unknown.append(key)
            continue
        title = model.model_fields[key].title or key
        kind = _kind(model, key)
        if kind == "bool":
            if not isinstance(value, bool):
                errors.append(f"options.{key}（{title}）应为 true 或 false，实际是 {value!r}")
                continue
        elif kind == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                errors.append(f"options.{key}（{title}）应为整数，实际是 {value!r}")
                continue
            low, high = _bounds(model, key)
            if (low is not None and value < low) or (high is not None and value > high):
                errors.append(f"options.{key}（{title}）应在 {low}–{high} 之间，实际是 {value}")
                continue
        else:
            allowed = _choices(model, key) or ()
            if value not in allowed:
                errors.append(
                    f"options.{key}（{title}）只能是 {' / '.join(allowed)} 之一，实际是 {value!r}",
                )
                continue
        accepted[key] = value
    diff = model(**accepted).diff_from_default() if not errors else {}
    return diff, errors, unknown
