"""房规文件：把一桌的村规导出成 YAML，或把别人的文件导入前先校验、预览。

格式见设计文档 6.3。几条硬约定：

- **导入只解析数据，不执行任何东西**。只用 ``yaml.safe_load``，参数逐项过组件 schema 的
  严格校验，越界与类型错误逐条报出，什么都不悄悄修正。
- **导出的是「预设之上的差异」**：只写改过的参数，文件小、人读得懂，日后预设默认值调整，
  没改过的项会跟着走。
- **向后兼容**：``format_version`` 每升一次，在 :data:`_MIGRATIONS` 里补一个纯函数把旧格式
  迁到新格式；导入时逐级迁移。比当前更新的版本直接拒绝——旧程序读不懂新字段。

本模块只认识规则系统（RuleSystem），不碰数据库；落库由 rule_options_service 负责。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

import yaml

from app.rules import data as rule_data
from app.rules.kernel import HOUSE_RULES_FORMAT_VERSION, RuleSystem

FORMAT = "coc-player/house-rules"
#: 文件大小上限。房规是几十行的东西，超过这个量级多半是传错了文件。
MAX_BYTES = 64 * 1024

_KNOWN_KEYS = {
    "format", "format_version", "rule_system", "base_preset", "meta", "options", "table_notes",
    "data",
}


def _v1_to_v2(doc: dict) -> dict:
    """v1 没有数据表段；v2 新增 ``data``（表名 → 补丁行）。v1 文件迁过来就是「没改数据」。"""
    return {**doc, "data": doc.get("data") or {}, "format_version": 2}


#: format_version → 把该版本迁到下一版本的纯函数。导入时逐级迁到当前版本。
_MIGRATIONS: dict[int, Callable[[dict], dict]] = {1: _v1_to_v2}


@dataclass
class HouseRulesPreview:
    """导入预览：``errors`` 为空才可以落库；``warnings`` 只提示，不阻止导入。"""

    options: dict = field(default_factory=dict)
    table_notes: str = ""
    #: 数据表补丁（表名 → 补丁行），已严格校验
    data: dict = field(default_factory=dict)
    #: 补丁打在官方数据上之后的整张表（由服务层填，供规则页载入草稿）
    tables: dict = field(default_factory=dict)
    name: str = ""
    description: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def export_text(
    system: RuleSystem, options_diff: dict, table_notes: str, *,
    data: dict | None = None, name: str = "", description: str = "", today: date | None = None,
) -> str:
    doc = {
        "format": FORMAT,
        "format_version": HOUSE_RULES_FORMAT_VERSION,
        "rule_system": system.id,
        "base_preset": f"{system.id}@{system.version}",
        "meta": {
            "name": name or f"{system.title} 房规",
            "description": description,
            "exported_at": (today or date.today()).isoformat(),
        },
        # 按规则页的顺序写（检定、幸运、伤害与状态……），人读起来与界面一一对应
        "options": {
            key: options_diff[key]
            for comp in system.active_components for key in comp.option_keys
            if key in options_diff
        },
        "table_notes": table_notes or "",
        # 数据表补丁按组件顺序写；只写有改动的表
        "data": {t.name: (data or {})[t.name] for t in system.data_tables if (data or {}).get(t.name)},
    }
    body = yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=100)
    return (
        "# CoC Player 房规文件。导入时只读取数据，不执行任何代码。\n"
        "# options 里只列与官方规则不同的参数；没写的都照规则书原文。\n"
        "# data 里是对数据表（武器、职业、技能……）的改动：按主键对齐，只写改了的字段；\n"
        "# 写 $delete: true 删除一行，主键不存在的行即新增。\n"
        + body
    )


def parse_text(system: RuleSystem, text: str, *, notes_max: int) -> HouseRulesPreview:
    preview = HouseRulesPreview()
    if len(text.encode("utf-8")) > MAX_BYTES:
        preview.errors.append(f"文件超过 {MAX_BYTES // 1024} KB，不像是房规文件")
        return preview
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as e:
        preview.errors.append(f"不是合法的 YAML：{_yaml_reason(e)}")
        return preview
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        preview.errors.append(f"不是房规文件（缺少 format: {FORMAT}）")
        return preview

    version = doc.get("format_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        preview.errors.append("format_version 应为正整数")
        return preview
    if version > HOUSE_RULES_FORMAT_VERSION:
        preview.errors.append(
            f"这份文件是更新版本的格式（v{version}），当前应用只认识到 "
            f"v{HOUSE_RULES_FORMAT_VERSION}，请先升级应用",
        )
        return preview
    while version < HOUSE_RULES_FORMAT_VERSION:
        doc = _MIGRATIONS[version](doc)
        version += 1

    if doc.get("rule_system") != system.id:
        preview.errors.append(
            f"这份房规是给「{doc.get('rule_system')}」的，当前规则系统是「{system.id}」",
        )
        return preview

    expected = f"{system.id}@{system.version}"
    if doc.get("base_preset") not in (None, expected):
        preview.warnings.append(
            f"文件基于官方预设 {doc.get('base_preset')}，本机是 {expected}：没写进文件的参数"
            "会按本机的官方默认值走",
        )
    extra = sorted(str(k) for k in doc if k not in _KNOWN_KEYS)
    if extra:
        preview.warnings.append(f"忽略不认识的字段：{', '.join(extra)}")

    meta = doc.get("meta") or {}
    if isinstance(meta, dict):
        preview.name = str(meta.get("name") or "")
        preview.description = str(meta.get("description") or "")

    options, errors, unknown = system.parse_options_strict(doc.get("options"))
    preview.errors += errors
    if unknown:
        preview.warnings.append(
            f"本机没有这些参数，导入时忽略：{', '.join(unknown)}"
            "（可能来自本机没装的组件，或是拼错了）",
        )
    preview.options = options

    data = doc.get("data")
    if data is not None and not isinstance(data, dict):
        preview.errors.append("data 应为「表名: 补丁行列表」的映射")
    else:
        known = {t.name for t in system.data_tables}
        unknown_tables = sorted(str(k) for k in (data or {}) if k not in known)
        if unknown_tables:
            preview.warnings.append(
                f"本机没有这些数据表，导入时忽略：{', '.join(unknown_tables)}",
            )
        for table in system.data_tables:
            patches = (data or {}).get(table.name)
            if patches is None:
                continue
            norm, errs = rule_data.validate_patch(table, table.official_rows, patches)
            preview.errors += errs
            if norm:
                preview.data[table.name] = norm

    notes = doc.get("table_notes")
    if notes is None:
        notes = ""
    if not isinstance(notes, str):
        preview.errors.append("table_notes 应为一段文字")
    elif len(notes.strip()) > notes_max:
        preview.errors.append(f"桌面约定超过 {notes_max} 字（实际 {len(notes.strip())} 字）")
    else:
        preview.table_notes = notes.strip()
    return preview


def _yaml_reason(e: yaml.YAMLError) -> str:
    mark = getattr(e, "problem_mark", None)
    problem = getattr(e, "problem", None) or "格式错误"
    return f"第 {mark.line + 1} 行：{problem}" if mark is not None else str(problem)
