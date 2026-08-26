"""本局生效的家规：模组默认 → 会话覆盖，两层合并后交给引擎。

与 ``style_presets`` 同一套层级约定：会话没设的项继承模组的默认值，模组也没设则一律
RAW。合并只有「后者覆盖前者」一条规则——家规是标量参数，不存在多来源叠加同一个值，
引进优先级只会让「我改的这项到底生效没有」变得不可解释。

引擎侧一律**读时覆盖**：这里返回的 dict 只在结算那一刻被读，绝不写回角色卡或事件。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.module import Module
from app.models.session import GameSession
from app.rules.coc import options as coc_options


def effective(db: Session, game_session: GameSession | None) -> dict:
    """取本局生效的家规 dict（可直接喂给 ``engine.resolve_check(options=...)``）。"""
    if game_session is None:
        return {}
    module = db.get(Module, game_session.module_id) if game_session.module_id else None
    return coc_options.merge(
        getattr(module, "default_rule_options", None),
        getattr(game_session, "rule_options", None),
    )


def effective_by_id(db: Session, session_id: str) -> dict:
    """只有 session_id 时的取法（会话通常已在身份映射里，这次 get 很便宜）。"""
    return effective(db, db.get(GameSession, session_id))


def normalized(raw: dict | None) -> dict:
    """把外部提交的家规白名单化 + 钳进合法区间，只留与 RAW 不同的项。

    落库前一律过这一道：家规是房主从界面填的外部输入，非法值不该有机会流进掷骰逻辑
    （「大成功阈值 = 100」会让每一骰都是大成功）。只存差异项，日后 RAW 默认值调整时，
    没显式改过那一项的存档会跟着走，不会被一份陈旧的全量快照钉死。
    """
    return coc_options.from_dict(raw).diff_from_default()


def resolved_view(db: Session, game_session: GameSession | None) -> dict:
    """给界面看的完整生效值（含未被改动的默认项），供设置面板回显。"""
    return coc_options.from_dict(effective(db, game_session)).to_dict()
