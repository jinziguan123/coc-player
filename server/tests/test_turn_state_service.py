"""回合状态服务的兼容与边界回归。

拆表分支曾有两个隐患：
1. 旧迁移把回合确认直接铺在 ``turn_state`` 顶层，新代码按嵌套 ``turn_confirm`` 读写；
2. ``commit_turn`` 清确认态时可能误伤 ``pending_checks`` 等常驻键。
本文件把两条都钉住。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.models.character import Character
from app.models.event_log import EventLog  # noqa: F401 —— 注册表，供 commit_turn 查询
from app.models.module import Module
from app.models.session import GameSession
from app.models.session_participant import SessionParticipant  # noqa: F401
from app.services.turn_state_service import (
    commit_turn,
    set_turn_confirm,
    turn_confirm_state,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine)
    session = maker()
    module = Module(title="测试模组", rule_system="coc", scenes=[], npcs=[], clues=[])
    char = Character(name="调查员", rule_system="coc", is_player=True)
    session.add_all([module, char])
    session.flush()
    game = GameSession(
        module_id=module.id,
        status="active",
        player_character_id=char.id,
    )
    game.participants.append(
        SessionParticipant(
            character_id=char.id,
            role="human",
            is_primary=True,
            seat_order=0,
            claimed=True,
            ready=True,
            identity_version=2,
        )
    )
    session.add(game)
    session.flush()
    game.turn_state = {char.id: True}  # 旧迁移口径：确认项直接铺在顶层
    session.commit()
    yield session
    engine.dispose()


def test_legacy_top_level_turn_confirm_is_still_readable(db):
    """旧迁移铺在 turn_state 顶层的确认项不能被当成 0 人确认。"""
    sid = db.query(GameSession).one().id
    state = turn_confirm_state(db, sid)
    assert state["confirmed_ids"] == [db.query(Character).one().id]
    assert state["ready"] is True


def test_set_turn_confirm_migrates_to_nested_shape(db):
    """第一次写确认态时收口到 ``turn_confirm``，不再继续污染顶层。"""
    sid = db.query(GameSession).one().id
    cid = db.query(Character).one().id
    set_turn_confirm(db, sid, cid, False)
    game = db.query(GameSession).one()
    assert game.turn_state == {"turn_confirm": {}}
    set_turn_confirm(db, sid, cid, True)
    game = db.query(GameSession).one()
    assert game.turn_state == {"turn_confirm": {cid: True}}
    assert turn_confirm_state(db, sid)["ready"] is True


def test_commit_turn_preserves_pending_checks(db):
    """清确认态只动 ``turn_confirm``，不碰待投检定。"""
    game = db.query(GameSession).one()
    cid = db.query(Character).one().id
    game.turn_state = {
        "turn_confirm": {cid: True},
        "pending_checks": {"p1": {"id": "p1"}},
    }
    db.commit()
    commit_turn(db, game.id)
    db.refresh(game)
    assert game.turn_state == {
        "turn_confirm": {},
        "pending_checks": {"p1": {"id": "p1"}},
    }
