"""组件数据表：官方数据（YAML）+ 分层补丁（模组 → 村规）。

一张数据表由某个组件声明（:class:`DataTable`），官方数据放在组件旁的 ``data/<表名>.yaml``，
每一行都过 ``row_model``（pydantic）校验。房主与模组作者的改动是**补丁**，按行的主键对齐：

- ``{"<主键>": "斧头", "dam": "1D8+DB"}``：改已有行的某几个字段（只写改了的）；
- 主键不存在的行：新增，须是完整的一行；
- ``{"<主键>": "弓箭", "$delete": true}``：删掉这一行。

与村规参数同一个原则：**只存差异**。官方数据日后修订时，没被改过的行与字段会跟着走。

两种解析，对应两种来源（同 :mod:`app.rules.config`）：

- :func:`apply_patches`：已落库的补丁。逐行宽松处理——某一行坏了就跳过这一行（记日志），
  不让一处坏数据拖垮整局；
- :func:`validate_patch`：界面提交 / 房规文件导入。逐行严格校验，错误带行号与字段路径。

**表在结算中的流向**：Ruleset 把合并后的表附在注入给组件函数的 ``options`` 上（见
:meth:`app.rules.config.RuleConfig.table`），所以「本局规则」= 参数 + 数据表，业务层调用写法不变。
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

DELETE = "$delete"


@dataclass(frozen=True, eq=False)
class DataTable:
    """一张可被房主/模组作者改动的数据表。"""

    name: str
    title: str
    row_model: type[BaseModel]
    #: 行主键字段（同一表内唯一）；补丁按它对齐。
    key: str
    #: 官方数据文件（YAML 列表）。
    source: Path
    description: str = ""
    #: 行列表 → 组件算法使用的运行时形态（默认：不可变的行元组）。
    build: Callable[[tuple[dict, ...]], Any] = tuple
    #: 行的显示名取哪个字段（默认即主键；疯狂症状的主键是英文标识，显示名是 label）。
    display: str = ""
    #: 一行 → 给 KP 看的要点（如武器「格斗(斗殴)，伤害 2D8+DB」）。房主改过的行据此告知 KP。
    brief: Callable[[dict], str] | None = None

    @cached_property
    def official_rows(self) -> tuple[dict, ...]:
        """官方数据：读 YAML、逐行校验、规范化。坏数据在启动时就报出来。"""
        raw = yaml.safe_load(self.source.read_text(encoding="utf-8")) or []
        rows = tuple(self.row_model(**r).model_dump() for r in raw)
        dup = _duplicates(r[self.key] for r in rows)
        if dup:
            raise ValueError(f"官方数据 {self.name} 主键重复：{dup}")
        return rows

    @cached_property
    def official(self) -> Any:
        return self.build(self.official_rows)

    def runtime(self, rows: tuple[dict, ...]) -> Any:
        return self.build(rows)

    def schema(self) -> dict:
        return self.row_model.model_json_schema()


# ── 补丁 ─────────────────────────────────────────────────────────────────


def apply_patches(table: DataTable, base: Iterable[dict], patches: Iterable[dict] | None) -> tuple[dict, ...]:
    """把一层补丁打到 ``base`` 上（宽松：坏行跳过并记日志）。行序：原有行保持原位，新增行追加。"""
    rows: dict[Any, dict] = {r[table.key]: dict(r) for r in base}
    for i, patch in enumerate(patches or []):
        if not isinstance(patch, dict) or table.key not in patch:
            logger.warning("数据表 %s 第 %d 条补丁缺主键 %s，已跳过", table.name, i + 1, table.key)
            continue
        k = patch[table.key]
        if patch.get(DELETE):
            rows.pop(k, None)
            continue
        merged = {**rows.get(k, {}), **{f: v for f, v in patch.items() if f != DELETE}}
        try:
            rows[k] = table.row_model(**merged).model_dump()
        except ValidationError as e:
            logger.warning("数据表 %s 的补丁行 %r 无效，已跳过：%s", table.name, k, e.errors()[:1])
    return tuple(rows.values())


def validate_patch(table: DataTable, base: Iterable[dict], patches: Any, *, path: str = "") -> tuple[list[dict], list[str]]:
    """严格校验一层补丁（相对 ``base``）。返回 (规范化后的补丁, 错误)。错误非空时不该落库。"""
    where = path or f"data.{table.name}"
    if patches is None:
        return [], []
    if not isinstance(patches, list):
        return [], [f"{where} 应为行的列表"]
    existing = {r[table.key]: r for r in base}
    seen: set[Any] = set()
    out: list[dict] = []
    errors: list[str] = []
    for i, patch in enumerate(patches):
        at = f"{where}[{i}]"
        if not isinstance(patch, dict):
            errors.append(f"{at} 应为一行（字段: 取值）")
            continue
        k = patch.get(table.key)
        if k in (None, ""):
            errors.append(f"{at} 缺少主键 {table.key}")
            continue
        if k in seen:
            errors.append(f"{at}（{k}）在同一份补丁里出现了两次")
            continue
        seen.add(k)
        if patch.get(DELETE):
            if k not in existing:
                errors.append(f"{at}（{k}）要删除的行不存在")
                continue
            out.append({table.key: k, DELETE: True})
            continue
        unknown = sorted(f for f in patch if f not in table.row_model.model_fields)
        if unknown:
            errors.append(f"{at}（{k}）有不认识的字段：{', '.join(unknown)}")
            continue
        try:
            row = table.row_model(**{**existing.get(k, {}), **patch}).model_dump()
        except ValidationError as e:
            for err in e.errors():
                field_path = ".".join(str(p) for p in err["loc"])
                errors.append(f"{at}（{k}）.{field_path}：{_reason(err)}")
            continue
        out.append(_row_diff(table, existing.get(k), row))
    return [p for p in out if len(p) > 1 or DELETE in p], errors


def diff_rows(table: DataTable, base: Iterable[dict], desired: Iterable[dict]) -> list[dict]:
    """「想要的整张表」相对 ``base`` 的最小补丁：改了的行只记改了的字段，删掉的行记删除。"""
    base_by_key = {r[table.key]: r for r in base}
    desired = list(desired)
    keys = {r[table.key] for r in desired}
    patches = [_row_diff(table, base_by_key.get(r[table.key]), r) for r in desired]
    patches = [p for p in patches if len(p) > 1]
    patches += [{table.key: k, DELETE: True} for k in base_by_key if k not in keys]
    return patches


def normalize_desired(table: DataTable, rows: Any) -> tuple[list[dict], list[str]]:
    """编辑器提交的「整张表」：逐行校验、主键查重。返回 (规范化后的行, 错误)。"""
    if not isinstance(rows, list):
        return [], [f"{table.title}应为行的列表"]
    out: list[dict] = []
    errors: list[str] = []
    seen: set[Any] = set()
    for i, row in enumerate(rows):
        at = f"第 {i + 1} 行"
        if not isinstance(row, dict):
            errors.append(f"{at} 不是一行数据")
            continue
        try:
            norm = table.row_model(**row).model_dump()
        except ValidationError as e:
            for err in e.errors():
                field_path = ".".join(str(p) for p in err["loc"])
                title = _field_title(table, field_path)
                errors.append(f"{at}（{row.get(table.key) or '未命名'}）{title}：{_reason(err)}")
            continue
        if norm[table.key] in seen:
            errors.append(f"{at}：{norm[table.key]} 重复了（{_field_title(table, table.key)}必须唯一）")
            continue
        seen.add(norm[table.key])
        out.append(norm)
    return out, errors


def layer_view(table: DataTable, base: Iterable[dict], patches: Iterable[dict] | None) -> dict:
    """编辑器看一层补丁的样子：合并后的行、每行状态（官方/已修改/新增）、被删掉的官方行。"""
    base = tuple(base)
    patches = list(patches or [])
    merged = apply_patches(table, base, patches)
    base_keys = {r[table.key] for r in base}
    touched = {p[table.key] for p in patches if not p.get(DELETE)}
    deleted = {p[table.key] for p in patches if p.get(DELETE)}
    status = {
        r[table.key]: ("added" if r[table.key] not in base_keys
                       else "modified" if r[table.key] in touched else "official")
        for r in merged
    }
    return {
        "rows": list(merged),
        "status": status,
        "deleted": [r for r in base if r[table.key] in deleted],
        # 被改过的官方行的原样（编辑器「恢复官方」用）
        "originals": {r[table.key]: r for r in base if r[table.key] in touched},
        "patches": patches,
    }


def summarize(table: DataTable, base: Iterable[dict], patches: Iterable[dict] | None) -> str:
    """一层补丁的一句话摘要，如「武器表：新增 2、修改 1」；没有改动时返回空串。"""
    keys = {r[table.key] for r in base}
    added = modified = deleted = 0
    for p in patches or []:
        if p.get(DELETE):
            deleted += 1
        elif p.get(table.key) in keys:
            modified += 1
        else:
            added += 1
    parts = [f"{n} {c}" for n, c in (("新增", added), ("修改", modified), ("删除", deleted)) if c]
    return f"{table.title}：{'、'.join(parts)}" if parts else ""


def changes(table: DataTable, base: Iterable[dict], rows: Iterable[dict]) -> list[tuple[str, str]]:
    """合并后的表相对 ``base`` 改了哪些行，给 KP 看：每项 (带要点的全文, 只有名字的短文)。

    顺序：新增 → 修改 → 删除；与补丁怎么分层无关——KP 只关心最终生效的是什么。
    """
    def name_of(row: dict) -> str:
        return str(row.get(table.display) or row[table.key]) if table.display else str(row[table.key])

    def item(verb: str, row: dict, detail: bool = True) -> tuple[str, str]:
        short = f"{verb} {name_of(row)}"
        text = table.brief(row) if (detail and table.brief) else ""
        return (f"{short}（{text}）" if text else short), short

    base_by_key = {r[table.key]: r for r in base}
    rows = tuple(rows)
    keys = {r[table.key] for r in rows}
    added = [item("新增", r) for r in rows if r[table.key] not in base_by_key]
    modified = [item("修改", r) for r in rows
                if r[table.key] in base_by_key and base_by_key[r[table.key]] != r]
    deleted = [item("删除", r, detail=False) for k, r in base_by_key.items() if k not in keys]
    return added + modified + deleted


def digest(layers: Iterable[Any]) -> str:
    """补丁层的内容摘要：作为生效规则缓存键的一部分。"""
    blob = json.dumps(list(layers), ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def frozen_mapping(rows: tuple[dict, ...], key: str, value: str) -> MappingProxyType:
    """行列表 → 只读映射（如技能表：技能名 → 起始值）。"""
    return MappingProxyType({r[key]: r[value] for r in rows})


# ── 内部 ─────────────────────────────────────────────────────────────────


def _row_diff(table: DataTable, before: dict | None, after: dict) -> dict:
    if before is None:
        return dict(after)
    out = {table.key: after[table.key]}
    out.update({f: v for f, v in after.items() if f != table.key and before.get(f) != v})
    return out


def _field_title(table: DataTable, path: str) -> str:
    top = path.split(".")[0]
    info = table.row_model.model_fields.get(top)
    return (info.title if info and info.title else top) + (path[len(top):] if "." in path else "")


def _duplicates(values: Iterable[Any]) -> list[Any]:
    seen: set[Any] = set()
    dup: list[Any] = []
    for v in values:
        if v in seen:
            dup.append(v)
        seen.add(v)
    return dup


def _reason(err: dict) -> str:
    kind = err.get("type", "")
    ctx = err.get("ctx") or {}
    if kind == "missing":
        return "必填"
    if kind in ("greater_than_equal", "less_than_equal"):
        return f"超出范围（{ctx}）"
    if kind == "string_pattern_mismatch":
        return "格式不对"
    return err.get("msg", "取值无效")
