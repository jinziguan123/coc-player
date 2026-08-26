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


def village_options(db: Session, rule_system: str) -> dict:
    """某套规则系统的村规；没配过则空 dict（＝全照规则原文）。"""
    row = db.get(RuleSystemOptions, (rule_system or DEFAULT_RULE_SYSTEM).strip())
    return dict(row.options or {}) if row else {}


def save_village_options(db: Session, rule_system: str, raw: dict | None) -> dict:
    """整份替换某套规则系统的村规，返回落库后的差异项。"""
    rule_system = (rule_system or DEFAULT_RULE_SYSTEM).strip()
    row = db.get(RuleSystemOptions, rule_system)
    if row is None:
        row = RuleSystemOptions(rule_system=rule_system)
        db.add(row)
    row.options = normalized(raw)
    db.commit()
    return dict(row.options)


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
