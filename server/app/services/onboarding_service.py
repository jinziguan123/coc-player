from __future__ import annotations

from copy import deepcopy

from sqlalchemy.orm import Session

from app.content.onboarding import SAMPLE_CHARACTER, SAMPLE_MODULE, SAMPLE_SLUG
from app.models.character import Character
from app.models.module import Module
from app.models.session import GameSession
from app.models.session_participant import SessionParticipant
from app.services import session_service


def _sample_module(db: Session) -> Module | None:
    for module in db.query(Module).all():
        world = module.world_setting or {}
        if (
            world.get("source") == "trpg-player-original"
            and world.get("sample_slug") == SAMPLE_SLUG
        ):
            return module
    return None


def _active_session(db: Session, module_id: str, token: str) -> GameSession | None:
    return (
        db.query(GameSession)
        .join(SessionParticipant, SessionParticipant.session_id == GameSession.id)
        .filter(
            GameSession.module_id == module_id,
            GameSession.status.in_(["setup", "active", "paused"]),
            SessionParticipant.owner_token == token,
            SessionParticipant.is_primary.is_(True),
        )
        .first()
    )


def _ensure_module(db: Session) -> Module:
    module = _sample_module(db)
    if module is not None:
        return module
    module = Module(**deepcopy(SAMPLE_MODULE))
    db.add(module)
    db.flush()
    return module


def _ensure_character(db: Session, module: Module, token: str) -> Character:
    occupied = session_service.active_character_ids(db)
    candidates = (
        db.query(Character)
        .filter(
            Character.module_id == module.id,
            Character.owner_token == token,
            Character.is_player.is_(True),
        )
        .all()
    )
    for character in candidates:
        if character.id not in occupied:
            return character

    character = Character(
        module_id=module.id,
        owner_token=token,
        **deepcopy(SAMPLE_CHARACTER),
    )
    db.add(character)
    db.flush()
    return character


def start_onboarding(db: Session, token: str) -> tuple[GameSession, bool]:
    """创建或复用当前玩家的原创示例单人会话。"""
    if not token:
        raise ValueError("缺少玩家身份")

    try:
        module = _sample_module(db)
        if module is not None:
            existing = _active_session(db, module.id, token)
            if existing is not None:
                return existing, True

        module = _ensure_module(db)
        character = _ensure_character(db, module, token)
        game = session_service.create_session(
            db,
            module.id,
            [{"character_id": character.id, "role": "human", "is_primary": True}],
            creator_token=token,
            commit=False,
        )
        # 引导流程**显式跳过大厅**。建局恒落 setup 是给「建局＝建房」这个心智服务的，
        # 而新手引导的全部意义就是把人直接放进一局游戏里——先让他面对一个只有自己、
        # 且已经配好的房间，多一步没有任何信息量的确认，正好卡在最不该有摩擦的地方。
        # 这里是唯一的例外，且只对它自己造的这一局生效。
        game.status = "active"
        db.commit()
        db.refresh(game)
        return game, False
    except Exception:
        db.rollback()
        raise
