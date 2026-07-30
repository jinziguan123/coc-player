"""角色卡不必属于某个模组。

联机时客人的角色卡存在**他自己**的库里（ADR-007：素材库的写操作只允许本机），
而模组在房主库里——跨库引用没有意义。此前 `CharacterCreate.module_id` 必填，
于是本地没有对应模组的客人根本建不了卡，只能干等房主替他导入。

模型层与 `CharacterRead` 一直是可空的，卡住的只有创建时的请求模型。
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (  # noqa: F401 — 注册全部表
    Base,
    Character,
    EventLog,
    GameSession,
    Module,
    SessionParticipant,
)
from app.schemas.character import CharacterCreate
from app.services import character_service, session_service


@pytest.fixture
def db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'chars.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_create_request_accepts_missing_module():
    data = CharacterCreate(name="无模组调查员", rule_system="coc")
    assert data.module_id is None


def test_creates_character_without_module(db):
    char = character_service.create_character(
        db, CharacterCreate(name="无模组调查员", rule_system="coc").model_dump()
    )
    assert char.id
    assert char.module_id is None


def test_character_without_module_can_claim_a_seat(db):
    """建出来还得能用——入座不该因为没模组而失败。"""
    module = Module(title="房主的模组", rule_system="coc", npcs=[], scenes=[])
    host = Character(name="房主角色", rule_system="coc", is_player=True)
    db.add_all([module, host])
    db.commit()

    session = session_service.create_session(
        db, module.id,
        [
            {"character_id": host.id, "role": "human", "is_primary": True},
            {"character_id": None, "role": "human"},
        ],
        creator_token="host-tok",
    )
    guest = character_service.create_character(
        db, CharacterCreate(name="客人的卡", rule_system="coc").model_dump()
    )
    empty = next(
        p for p in session_service.get_participants(db, session.id) if not p.character_id
    )

    session_service.claim_seat(db, session.id, empty.seat_order, guest.id, "guest-tok")
    seat = next(
        p for p in session_service.get_participants(db, session.id)
        if p.seat_order == empty.seat_order
    )
    assert seat.character_id == guest.id


def test_listing_by_module_ignores_unassigned_ones(db):
    """按模组筛选时，没归属的卡不该混进某个模组的列表里。"""
    module = Module(title="某模组", rule_system="coc", npcs=[], scenes=[])
    db.add(module)
    db.commit()

    character_service.create_character(
        db, CharacterCreate(name="属于模组的", rule_system="coc", module_id=module.id).model_dump()
    )
    character_service.create_character(
        db, CharacterCreate(name="无归属的", rule_system="coc").model_dump()
    )

    names = [c.name for c in character_service.list_characters(db, module.id)]
    assert names == ["属于模组的"]
    # 不带筛选时两张都在
    assert len(character_service.list_characters(db)) == 2
