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
from app.rules.coc import options as coc_options

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


def save_village_options(
    db: Session, rule_system: str, raw: dict | None, notes: str | None = None,
    enabled: bool | None = None,
) -> tuple[dict, str]:
    """整份替换某套规则系统的村规与桌面约定，返回落库后的 (差异项, 约定原文)。

    ``notes=None`` 表示本次不动桌面约定（只改参数时不必把整段文字回传一遍）；
    ``enabled=None`` 同理不动总开关。落库的始终是**配置本身**——关掉开关不清空它们，
    只是读的时候当作没配过。
    """
    rule_system = (rule_system or DEFAULT_RULE_SYSTEM).strip()
    row = db.get(RuleSystemOptions, rule_system)
    if row is None:
        row = RuleSystemOptions(rule_system=rule_system)
        db.add(row)
    row.options = normalized(raw)
    if notes is not None:
        row.table_notes = (notes or "").strip()[:TABLE_NOTES_MAX]
    if enabled is not None:
        row.enabled = bool(enabled)
    db.commit()
    return dict(row.options), row.table_notes or ""


def effective(db: Session, game_session: GameSession | None) -> dict:
    """取本局生效的规则改动（可直接喂给 ``engine.resolve_check(options=...)``）。"""
    if game_session is None:
        return {}
    module = db.get(Module, game_session.module_id) if game_session.module_id else None
    rule_system = getattr(module, "rule_system", None) or DEFAULT_RULE_SYSTEM
    return coc_options.merge(
        getattr(module, "default_rule_options", None),
        village_options(db, rule_system),
        getattr(game_session, "rule_options", None),
    )


def effective_by_id(db: Session, session_id: str) -> dict:
    """只有 session_id 时的取法（会话通常已在身份映射里，这次 get 很便宜）。"""
    return effective(db, db.get(GameSession, session_id))


def normalized(raw: dict | None) -> dict:
    """把外部提交的配置白名单化 + 钳进合法区间，只留与规则原文不同的项。

    落库前一律过这一道：这是从界面填进来的外部输入，非法值不该有机会流进掷骰逻辑
    （「大成功阈值 = 100」会让每一骰都是大成功）。只存差异项，日后规则默认值调整时，
    没显式改过的项会跟着走，不会被一份陈旧的全量快照钉死。
    """
    return coc_options.from_dict(raw).diff_from_default()


def resolved_view(db: Session, game_session: GameSession | None) -> dict:
    """给界面看的完整生效值（含未被改动的默认项），供设置面板回显。"""
    return coc_options.from_dict(effective(db, game_session)).to_dict()


def village_view(db: Session, rule_system: str) -> dict:
    """村规面板的回显值：村规叠在规则原文上的完整结果。"""
    return coc_options.from_dict(village_options(db, rule_system)).to_dict()


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
    module = db.get(Module, game_session.module_id) if game_session.module_id else None
    rule_system = getattr(module, "rule_system", None) or DEFAULT_RULE_SYSTEM
    diffs = coc_options.describe(coc_options.from_dict(effective(db, game_session)))
    notes = table_notes(db, rule_system)
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
