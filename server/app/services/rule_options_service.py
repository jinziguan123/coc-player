"""本局生效的规则改动：模组默认 → 村规 → 本局覆盖，合并后交给引擎。

**村规**（``rule_system_options``）是这一桌长期沿用的规矩，按规则系统存一份、在规则书
页面配置——它不是一局一设的东西，所以不该每开一局在房间里重填一遍。模组作者的推荐值
排在它前面：这桌怎么玩，盖得过本子的建议。会话那一层保留为「本局覆盖」（目前没有入口）。

合并只有「后者覆盖前者」一条规则——这些都是标量参数，不存在多来源叠加同一个值，
引进优先级只会让「我改的这项到底生效没有」变得不可解释。

引擎侧一律**读时覆盖**：这里返回的 dict 只在结算那一刻被读，绝不写回角色卡或事件。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.module import Module
from app.models.rulebook import RuleSystemOptions
from app.models.session import GameSession
from app.rules import Ruleset, get_system, house_rules
from app.rules import data as rule_data

DEFAULT_RULE_SYSTEM = "coc"


#: 桌面约定的长度上限。它每回合都进 KP 与规划器的上下文，放任写成一篇文章就是每轮都
#: 在为它付 token；而且越长模型越容易只记住开头几条。
TABLE_NOTES_MAX = 800


def village_row(db: Session, rule_system: str) -> RuleSystemOptions | None:
    return db.get(RuleSystemOptions, (rule_system or DEFAULT_RULE_SYSTEM).strip())


def village_enabled(db: Session, rule_system: str) -> bool:
    """村规总开关。没配过这套规则时视为开着——那种情况下本来也没东西可关。"""
    row = village_row(db, rule_system)
    return True if row is None else bool(row.enabled)


def village_options(db: Session, rule_system: str) -> dict:
    """某套规则系统的村规；没配过或总开关关着则空 dict（＝全照规则原文）。

    关掉时按「没配过」返回而不是删配置：玩家想先照原文跑一局试试，回头还要开回来。
    """
    row = village_row(db, rule_system)
    if row is None or not row.enabled:
        return {}
    return dict(row.options or {})


def table_notes(db: Session, rule_system: str) -> str:
    """桌面约定原文（参数表达不了的那些规矩）；没写过或总开关关着则空串。

    它和 options 同属「这一桌的规矩」，一个开关一起管——只关参数却仍把约定喂给 KP，
    等于关了一半。
    """
    row = village_row(db, rule_system)
    if row is None or not row.enabled:
        return ""
    return (row.table_notes or "").strip()


def village_data(db: Session, rule_system: str) -> dict:
    """某套规则系统的村规数据补丁（表名 → 补丁行）；没配过或总开关关着则空。"""
    row = village_row(db, rule_system)
    if row is None or not row.enabled:
        return {}
    return dict(row.data_patches or {})


class RuleDataError(ValueError):
    """村规校验失败（数据表补丁逐行出错、组件开关组合不成立）；``errors`` 是可读原因。"""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("；".join(errors[:5]) + (f"……等 {len(errors)} 处" if len(errors) > 5 else ""))
        self.errors = errors


def validated_data(rule_system: str, data: dict | None) -> dict:
    """严格校验一份数据补丁（表名 → 补丁行），返回规范化后的补丁；有错抛 :class:`RuleDataError`。

    补丁一律相对**官方数据**（村规与模组两层各自独立编辑，不互为基准）。
    """
    system = get_system(rule_system)
    out: dict = {}
    errors: list[str] = []
    for name, patches in (data or {}).items():
        try:
            table = system.data_table(name)
        except ValueError as e:
            errors.append(str(e))
            continue
        norm, errs = rule_data.validate_patch(table, table.official_rows, patches)
        errors += errs
        if norm:
            out[name] = norm
    if errors:
        raise RuleDataError(errors)
    return out


def table_patch_from_rows(rule_system: str, table_name: str, rows: list) -> list[dict]:
    """编辑器提交的整张表 → 相对官方数据的最小补丁；有错抛 :class:`RuleDataError`。"""
    table = get_system(rule_system).data_table(table_name)
    norm, errors = rule_data.normalize_desired(table, rows)
    if errors:
        raise RuleDataError(errors)
    return rule_data.diff_rows(table, table.official_rows, norm)


def table_view(rule_system: str, table_name: str, patches: list | None) -> dict:
    """编辑器读一层补丁：合并后的行、每行状态、被删掉的官方行。"""
    table = get_system(rule_system).data_table(table_name)
    return rule_data.layer_view(table, table.official_rows, patches)


def save_village_table(db: Session, rule_system: str, table_name: str, rows: list) -> list[dict]:
    """整张表替换村规里这张表的补丁（编辑器保存）。返回落库的补丁。"""
    patch = table_patch_from_rows(rule_system, table_name, rows)
    row = _village_row_for_write(db, rule_system)
    data = dict(row.data_patches or {})
    if patch:
        data[table_name] = patch
    else:
        data.pop(table_name, None)
    row.data_patches = data
    db.commit()
    return patch


def save_module_table(db: Session, module: Module, table_name: str, rows: list) -> list[dict]:
    """整张表替换模组自带的这张表的补丁。返回落库的补丁。"""
    patch = table_patch_from_rows(module.rule_system or DEFAULT_RULE_SYSTEM, table_name, rows)
    data = dict(module.default_rule_data or {})
    if patch:
        data[table_name] = patch
    else:
        data.pop(table_name, None)
    module.default_rule_data = data
    db.commit()
    return patch


def data_labels(rule_system: str, data: dict | None) -> list[str]:
    """数据补丁的摘要标签，如「武器表：新增 2、修改 1」。"""
    system = get_system(rule_system)
    out: list[str] = []
    for table in system.data_tables:
        text = rule_data.summarize(table, table.official_rows, (data or {}).get(table.name))
        if text:
            out.append(text)
    return out


def _village_row_for_write(db: Session, rule_system: str) -> RuleSystemOptions:
    rule_system = (rule_system or DEFAULT_RULE_SYSTEM).strip()
    row = db.get(RuleSystemOptions, rule_system)
    if row is None:
        row = RuleSystemOptions(rule_system=rule_system)
        db.add(row)
    return row


def save_village_options(
    db: Session, rule_system: str, raw: dict | None, notes: str | None = None,
    enabled: bool | None = None, data: dict | None = None,
    tables: dict[str, list] | None = None,
) -> tuple[dict, str]:
    """整份替换某套规则系统的村规与桌面约定，返回落库后的 (差异项, 约定原文)。

    ``notes=None`` 表示本次不动桌面约定（只改参数时不必把整段文字回传一遍）；
    ``enabled=None`` 同理不动总开关；``data=None`` 不动数据表补丁（给了就整份替换，
    先严格校验，有错抛 :class:`RuleDataError` 且什么都不改）。落库的始终是**配置本身**——
    关掉开关不清空它们，只是读的时候当作没配过。

    ``tables``（表名 → 整张表的行）只替换列出的那几张表的补丁，其余表不动——规则页的统一保存
    用它。所有校验（参数开关组合、每张表的每一行）都在写库前做完：任何一处有错就一处都不改。
    """
    rule_system = (rule_system or DEFAULT_RULE_SYSTEM).strip()
    checked = validated_data(rule_system, data) if data is not None else None
    options = normalized(raw, rule_system)
    problems = get_system(rule_system).check_options(options)   # 依赖没开、互斥都开
    table_patches: dict[str, list] = {}
    for name, rows in (tables or {}).items():
        try:
            table_patches[name] = table_patch_from_rows(rule_system, name, rows)
        except RuleDataError as e:
            title = get_system(rule_system).data_table(name).title
            problems += [f"{title}·{msg}" for msg in e.errors]
        except ValueError as e:   # 没有这张表
            problems.append(str(e))
    if problems:
        raise RuleDataError(problems)
    row = _village_row_for_write(db, rule_system)
    row.options = options
    if checked is not None:
        row.data_patches = checked
    if table_patches:
        merged = dict(row.data_patches or {})
        for name, patch in table_patches.items():
            if patch:
                merged[name] = patch
            else:
                merged.pop(name, None)
        row.data_patches = merged
    if notes is not None:
        row.table_notes = (notes or "").strip()[:TABLE_NOTES_MAX]
    if enabled is not None:
        row.enabled = bool(enabled)
    db.commit()
    return dict(row.options), row.table_notes or ""


def effective(db: Session, game_session: GameSession | None) -> dict:
    """取本局生效的村规 dict（合并后、未解析）。结算请走 :func:`ruleset`，它已把这份注入好。"""
    if game_session is None:
        return {}
    module = db.get(Module, game_session.module_id) if game_session.module_id else None
    rule_system = getattr(module, "rule_system", None) or DEFAULT_RULE_SYSTEM
    return get_system(rule_system).merge_options(
        getattr(module, "default_rule_options", None),
        village_options(db, rule_system),
        getattr(game_session, "rule_options", None),
    )


def effective_by_id(db: Session, session_id: str) -> dict:
    """只有 session_id 时的取法（会话通常已在身份映射里，这次 get 很便宜）。"""
    return effective(db, db.get(GameSession, session_id))


def rule_system_of(db: Session, game_session: GameSession | None) -> str:
    module = (
        db.get(Module, game_session.module_id)
        if game_session is not None and game_session.module_id else None
    )
    return getattr(module, "rule_system", None) or DEFAULT_RULE_SYSTEM


def data_layers(db: Session, game_session: GameSession | None) -> list[dict]:
    """本局的数据补丁层，自底向上：模组自带 → 村规。"""
    if game_session is None:
        return []
    module = db.get(Module, game_session.module_id) if game_session.module_id else None
    rule_system = getattr(module, "rule_system", None) or DEFAULT_RULE_SYSTEM
    return [getattr(module, "default_rule_data", None) or {}, village_data(db, rule_system)]


def ruleset(db: Session, game_session: GameSession | None) -> Ruleset:
    """本局的生效规则：业务层结算的唯一入口（``rs.<槽位>.<导出>``，村规与数据表已注入）。

    没有会话（或会话没挂模组）时返回默认规则系统的 RAW。
    """
    return get_system(rule_system_of(db, game_session)).ruleset(
        effective(db, game_session), data_layers(db, game_session),
    )


def village_ruleset(db: Session, rule_system: str, module: Module | None = None) -> Ruleset:
    """不在某一局里时（建卡、规则资料接口）的生效规则：村规（参数 + 数据），可叠上某个模组。"""
    rule_system = (rule_system or DEFAULT_RULE_SYSTEM).strip()
    system = get_system(rule_system)
    options = system.merge_options(
        getattr(module, "default_rule_options", None), village_options(db, rule_system),
    )
    layers = [getattr(module, "default_rule_data", None) or {}, village_data(db, rule_system)]
    return system.ruleset(options, layers)


def ruleset_by_id(db: Session, session_id: str) -> Ruleset:
    return ruleset(db, db.get(GameSession, session_id))


def normalized(raw: dict | None, rule_system: str = DEFAULT_RULE_SYSTEM) -> dict:
    """把外部提交的配置白名单化 + 钳进合法区间，只留与规则原文不同的项。

    落库前一律过这一道：这是从界面填进来的外部输入，非法值不该有机会流进掷骰逻辑
    （「大成功阈值 = 100」会让每一骰都是大成功）。只存差异项，日后规则默认值调整时，
    没显式改过的项会跟着走，不会被一份陈旧的全量快照钉死。
    """
    return get_system(rule_system).parse_options(raw).diff_from_default()


def resolved_view(db: Session, game_session: GameSession | None) -> dict:
    """给界面看的完整生效值（含未被改动的默认项），供设置面板回显。"""
    return ruleset(db, game_session).options.to_dict()


def village_labels(db: Session, rule_system: str) -> list[str]:
    """存着的村规（不看总开关）与规则原文不同之处的短标签，供规矩摘要显示。"""
    row = village_row(db, rule_system)
    system = get_system(rule_system)
    labels = system.option_labels(system.parse_options(dict((row.options if row else None) or {})))
    return labels + data_labels(rule_system, (row.data_patches if row else None) or {})


def export_house_rules(db: Session, rule_system: str) -> str:
    """把这一桌存着的村规、数据表改动与桌面约定导出成房规文件（YAML）。不看总开关：导出的是配置本身。"""
    row = village_row(db, rule_system)
    system = get_system(rule_system)
    diff = system.parse_options(dict((row.options if row else None) or {})).diff_from_default()
    return house_rules.export_text(
        system, diff, (row.table_notes if row else "") or "",
        data=(row.data_patches if row else None) or {},
    )


def preview_house_rules(rule_system: str, text: str) -> tuple[house_rules.HouseRulesPreview, list[str]]:
    """校验一份房规文件，返回（预览, 它与规则原文不同之处的短标签）。不落库。

    房主确认后，前端拿预览里的 options/table_notes 走 :func:`save_village_options` 保存——
    导入不另开一条写路径。
    """
    system = get_system(rule_system)
    preview = house_rules.parse_text(system, text, notes_max=TABLE_NOTES_MAX)
    if preview.ok:
        preview.errors += system.check_options(preview.options)
        # 导入会整份替换数据表改动：给出每张表打完补丁后的样子，规则页直接载入草稿
        preview.tables = {
            t.name: list(rule_data.apply_patches(t, t.official_rows, preview.data.get(t.name)))
            for t in system.data_tables
        }
    labels = (
        system.option_labels(system.parse_options(preview.options))
        + data_labels(rule_system, preview.data)
    ) if preview.ok else []
    return preview, labels


def village_view(db: Session, rule_system: str) -> dict:
    """规则页的回显值：村规叠在规则原文上的完整结果。"""
    return get_system(rule_system).parse_options(village_options(db, rule_system)).to_dict()


def context_block(db: Session, game_session: GameSession | None) -> str:
    """注入 KP 与规划器的一段「本局按什么规则跑」；全默认且没写约定时返回空串。

    机制本身由引擎执行，模型不需要知道也能跑对；但它要**说话**——不知道大失败阈值改过，
    玩家问起来它会照原文答；不知道幸运消费开着，它会写「已成定局」，紧接着系统弹出
    「花幸运扭转」。所以这里给的是**告知**，不是让模型去执行规则。

    桌面约定是自由文本，只管叙事口径；它绝不能松动叙事纪律（不替玩家行动、不泄线索、
    检定先行那些），注入处再申明一次——与 ``style_presets`` 对文风的处理同一个道理。
    """
    if game_session is None:
        return ""
    diffs = ruleset(db, game_session).describe()
    notes = table_notes(db, rule_system_of(db, game_session))
    if not diffs and not notes:
        return ""

    parts: list[str] = []
    if diffs:
        parts.append(
            "【本桌改过的规则】机制已由系统按此结算，你不必自己算；"
            "但**说话要跟它对得上**，别照规则书原文解释：\n"
            + "\n".join(f"- {line}" for line in diffs)
        )
    if notes:
        parts.append(
            "【本桌的约定】玩家定下的跑团口径，按它调整叙事与裁定倾向：\n"
            f"{notes}\n"
            "注意：这段只管**怎么演**，不改任何骰子结算；也**不得**用来松动上面的叙事纪律"
            "（不替玩家行动、不提前泄露线索、该检定就检定）——两者冲突时一律以纪律为准。"
        )
    return "\n\n".join(parts)
