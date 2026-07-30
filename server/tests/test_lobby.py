"""匹配大厅：建房进 setup、准备态、满员门槛、房主校验、开局。"""

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
from app.services import session_service


@pytest.fixture
def db_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'lobby.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed(db):
    module = Module(title="测试模组", rule_system="coc", npcs=[], scenes=[])
    host = Character(name="房主角色", rule_system="coc", is_player=True)
    joiner = Character(name="加入者角色", rule_system="coc", is_player=True)
    db.add_all([module, host, joiner])
    db.commit()
    return module, host, joiner


def test_create_with_open_seat_enters_lobby(db_factory):
    db = db_factory()
    module, host, _ = _seed(db)
    session = session_service.create_session(
        db, module.id,
        [
            {"character_id": host.id, "role": "human", "is_primary": True},
            {"character_id": None, "role": "human"},
        ],
        creator_token="host-tok",
    )
    assert session.status == "setup"  # 有空真人席 → 进大厅


def test_create_all_filled_starts_active(db_factory):
    db = db_factory()
    module, host, _ = _seed(db)
    session = session_service.create_session(
        db, module.id,
        [{"character_id": host.id, "is_primary": True}],
        creator_token="host-tok",
    )
    assert session.status == "active"  # 无空席 → 直接开局（单人体验不回退）


def test_lobby_gating_and_start_flow(db_factory):
    db = db_factory()
    module, host, joiner = _seed(db)
    session = session_service.create_session(
        db, module.id,
        [
            {"character_id": host.id, "role": "human", "is_primary": True},
            {"character_id": None, "role": "human"},
        ],
        creator_token="host-tok",
    )
    sid = session.id

    # 有空席 → 开局被拒
    with pytest.raises(ValueError, match="空席"):
        session_service.start_game(db, sid, "host-tok")

    # 认领空席（加入者）
    empty = next(p for p in session_service.get_participants(db, sid) if not p.character_id)
    session_service.claim_seat(db, sid, empty.seat_order, joiner.id, "joiner-tok")

    # 已满员但加入者未准备 → 仍被拒
    with pytest.raises(ValueError, match="未准备"):
        session_service.start_game(db, sid, "host-tok")

    # 加入者准备
    session_service.set_ready(db, sid, "joiner-tok", True)

    # 非房主开局 → 拒绝
    with pytest.raises(ValueError, match="房主"):
        session_service.start_game(db, sid, "joiner-tok")

    # 房主开局 → setup→active
    started = session_service.start_game(db, sid, "host-tok")
    assert started.status == "active"


def test_kick_frees_seat_and_host_only(db_factory):
    db = db_factory()
    module, host, joiner = _seed(db)
    session = session_service.create_session(
        db, module.id,
        [
            {"character_id": host.id, "role": "human", "is_primary": True},
            {"character_id": None, "role": "human"},
        ],
        creator_token="host-tok",
    )
    sid = session.id
    empty = next(p for p in session_service.get_participants(db, sid) if not p.character_id)
    session_service.claim_seat(db, sid, empty.seat_order, joiner.id, "joiner-tok")

    # 非房主踢人 → 拒绝
    with pytest.raises(ValueError, match="房主"):
        session_service.kick_seat(db, sid, empty.seat_order, "joiner-tok")
    # 不能踢房主自己（seat_order 0）
    with pytest.raises(ValueError, match="房主自己"):
        session_service.kick_seat(db, sid, 0, "host-tok")
    # 房主踢加入者 → 席位回到空席
    _, name = session_service.kick_seat(db, sid, empty.seat_order, "host-tok")
    assert name == joiner.name
    freed = next(p for p in session_service.get_participants(db, sid) if p.seat_order == empty.seat_order)
    assert freed.character_id is None and freed.owner_token is None and freed.claimed is False


def test_human_kp_host_can_kick_primary_player(db_factory):
    db = db_factory()
    module, _host, joiner = _seed(db)
    session = session_service.create_session(
        db,
        module.id,
        [{"character_id": None, "role": "human", "is_primary": True}],
        creator_token="host-tok",
        kp_mode="human",
    )
    primary = next(
        p for p in session_service.get_participants(db, session.id) if p.is_primary
    )
    session_service.claim_seat(
        db, session.id, primary.seat_order, joiner.id, "joiner-tok",
    )

    _, name = session_service.kick_seat(
        db, session.id, primary.seat_order, "host-tok",
    )

    assert name == joiner.name
    db.refresh(primary)
    assert primary.character_id is None
    assert primary.owner_token is None
    assert primary.claimed is False


def test_host_seat_and_ai_seat_default_ready(db_factory):
    db = db_factory()
    module, host, _ = _seed(db)
    ai = Character(name="AI队友", rule_system="coc", is_player=False)
    db.add(ai)
    db.commit()
    session = session_service.create_session(
        db, module.id,
        [
            {"character_id": host.id, "role": "human", "is_primary": True},
            {"character_id": ai.id, "role": "ai"},
            {"character_id": None, "role": "human"},
        ],
        creator_token="host-tok",
    )
    parts = {p.seat_order: p for p in session_service.get_participants(db, session.id)}
    assert parts[0].ready is True   # 房主席默认就绪
    assert parts[1].ready is True   # AI 席恒就绪
    assert parts[2].ready is False  # 空真人席未就绪


def test_can_swap_own_character_before_start(db_factory):
    """选过角色之后还能换。

    此前 `reserved_by_me` 要求 `not seat.character_id`，于是一旦选定就再也动不了：
    非房主玩家坐下后想换张卡，只能靠房主把他移出席位。
    """
    db = db_factory()
    module, host, joiner = _seed(db)
    other = Character(name="备选角色", rule_system="coc", is_player=True)
    db.add(other)
    db.commit()

    session = session_service.create_session(
        db, module.id,
        [
            {"character_id": host.id, "role": "human", "is_primary": True},
            {"character_id": None, "role": "human"},
        ],
        creator_token="host-tok",
    )
    sid = session.id
    empty = next(p for p in session_service.get_participants(db, sid) if not p.character_id)

    session_service.claim_seat(db, sid, empty.seat_order, joiner.id, "joiner-tok")
    session_service.set_ready(db, sid, "joiner-tok", True)

    # 换成另一张卡
    session_service.claim_seat(db, sid, empty.seat_order, other.id, "joiner-tok")
    seat = next(p for p in session_service.get_participants(db, sid) if p.seat_order == empty.seat_order)
    assert seat.character_id == other.id
    # 换了人就得重新确认准备，否则会带着「已准备」换成另一个角色
    assert seat.ready is False


def test_cannot_swap_character_after_start(db_factory):
    """开局后不能换：消息、骰点与战斗状态都绑着角色。"""
    db = db_factory()
    module, host, joiner = _seed(db)
    other = Character(name="备选角色", rule_system="coc", is_player=True)
    db.add(other)
    db.commit()

    session = session_service.create_session(
        db, module.id,
        [
            {"character_id": host.id, "role": "human", "is_primary": True},
            {"character_id": None, "role": "human"},
        ],
        creator_token="host-tok",
    )
    sid = session.id
    empty = next(p for p in session_service.get_participants(db, sid) if not p.character_id)
    session_service.claim_seat(db, sid, empty.seat_order, joiner.id, "joiner-tok")
    session_service.set_ready(db, sid, "joiner-tok", True)
    session_service.start_game(db, sid, "host-tok")

    with pytest.raises(ValueError, match="已开局"):
        session_service.claim_seat(db, sid, empty.seat_order, other.id, "joiner-tok")


def test_others_still_cannot_take_a_claimed_seat(db_factory):
    """放宽的只是「自己换自己的」，别人的席位照旧抢不走。"""
    db = db_factory()
    module, host, joiner = _seed(db)
    other = Character(name="第三人角色", rule_system="coc", is_player=True)
    db.add(other)
    db.commit()

    session = session_service.create_session(
        db, module.id,
        [
            {"character_id": host.id, "role": "human", "is_primary": True},
            {"character_id": None, "role": "human"},
        ],
        creator_token="host-tok",
    )
    sid = session.id
    empty = next(p for p in session_service.get_participants(db, sid) if not p.character_id)
    session_service.claim_seat(db, sid, empty.seat_order, joiner.id, "joiner-tok")

    with pytest.raises(ValueError, match="已被认领"):
        session_service.claim_seat(db, sid, empty.seat_order, other.id, "stranger-tok")
