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


def test_建局恒进大厅且房主席已就绪(db_factory):
    """建局＝建房，单人局也一样落在 setup。

    从前这里是「无空席 → 直接 active」的快速开局，代价是心智不一致、且建完就改不动了
    （配错一个角色只能删档重来，也没法把席位改留给后来的真人）。多出的那一次点击靠
    「房主席建局即就绪」抵掉：他刚选完自己的角色，再跟自己确认一次准备纯属仪式。
    """
    db = db_factory()
    module, host, _ = _seed(db)
    session = session_service.create_session(
        db, module.id,
        [{"character_id": host.id, "is_primary": True}],
        creator_token="host-tok",
    )
    assert session.status == "setup"
    assert session_service.lobby_gaps(db, session.id) == []   # 门槛已满，进大厅点一下就开
    assert session_service.start_game(db, session.id, "host-tok").status == "active"


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
    db.refresh(session)
    assert session.player_character_id is None
    assert joiner.id not in session_service.active_character_ids(db)


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


def test_swapping_primary_character_syncs_player_character_id(db_factory):
    """房主在主角席换人后，快捷字段必须同步，否则旧角色会被幽灵占用。

    幽灵占用的后果是：角色管理里能看到这张卡，大厅的「可用调查员卡」里却
    永远没有它——因为 active_character_ids 同时读主角席和 player_character_id。
    """
    db = db_factory()
    module, host, _joiner = _seed(db)
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
    primary = next(
        p for p in session_service.get_participants(db, sid) if p.is_primary
    )
    assert session.player_character_id == host.id

    session_service.claim_seat(db, sid, primary.seat_order, other.id, "host-tok")

    db.refresh(session)
    assert session.player_character_id == other.id
    assert host.id not in session_service.active_character_ids(db)
    assert other.id in session_service.active_character_ids(db)


def test_active_character_ids_ignores_stale_shortcut_when_seats_exist(db_factory):
    """有席位记录的会话只按席位算占用，旧快捷字段的脏值不算数。

    这层防护让历史库里已经写坏的数据也能自愈：不需要先修 player_character_id，
    被幽灵占用的角色会立刻重新出现在大厅候选池里。
    """
    db = db_factory()
    module, host, _joiner = _seed(db)
    stale = Character(name="幽灵占用角色", rule_system="coc", is_player=True)
    db.add(stale)
    db.commit()

    session = session_service.create_session(
        db, module.id,
        [
            {"character_id": host.id, "role": "human", "is_primary": True},
            {"character_id": None, "role": "human"},
        ],
        creator_token="host-tok",
    )
    # 模拟历史 bug 留下的脏值：主角席实际是 host，快捷字段却还指着 stale。
    session.player_character_id = stale.id
    db.commit()

    occupied = session_service.active_character_ids(db)
    assert host.id in occupied
    assert stale.id not in occupied


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


# ── 大厅里的座位管理 ──
#
# 「模组是房间的身份（建房时定），座位是房间的内容（房间里配）」：建房那一屏只选本子与
# KP 模式，人数、谁坐哪、AI 队友用哪张卡，全部在大厅里调。


def _room(db, module, host_char=None):
    """建一个房间：默认连房主角色都不带（角色进大厅再挑）。"""
    seats = [{"character_id": host_char.id if host_char else None,
              "role": "human", "is_primary": True}]
    return session_service.create_session(db, module.id, seats, creator_token="host-tok")


def test_建房可以不带任何角色(db_factory):
    """创建者在上一屏只选了本子；角色是进大厅之后才挑的。"""
    db = db_factory()
    module, _host, _ = _seed(db)
    session = _room(db, module)
    assert session.status == "setup"
    assert session.player_character_id is None
    assert session_service.lobby_gaps(db, session.id)      # 空席仍被门槛拦住，不会漏出无角色的局


def test_加减座位(db_factory):
    db = db_factory()
    module, host, _ = _seed(db)
    session = _room(db, module, host)

    session_service.add_seat(db, session.id, "ai", "host-tok")
    session_service.add_seat(db, session.id, "human", "host-tok")
    parts = [p for p in session_service.get_participants(db, session.id) if p.role != "kp"]
    assert [p.role for p in parts] == ["human", "ai", "human"]
    assert next(p for p in parts if p.role == "ai").ready      # AI 席没有「谁来点准备」可言

    session_service.remove_seat(db, session.id, parts[-1].seat_order, "host-tok")
    assert len([p for p in session_service.get_participants(db, session.id) if p.role != "kp"]) == 2


def test_座位增删的边界(db_factory):
    db = db_factory()
    module, host, _ = _seed(db)
    session = _room(db, module, host)
    only_seat = session_service.get_participants(db, session.id)[0]

    # 唯一的座位既是房主的、也是最后一个——先撞上「不能删房主自己」这条更具体的
    with pytest.raises(ValueError, match="房主自己"):
        session_service.remove_seat(db, session.id, only_seat.seat_order, "host-tok")
    # 换个不属于房主的场景验「至少保留一个」：加一个 AI 席再把房主席之外的删到只剩一个
    session_service.add_seat(db, session.id, "ai", "host-tok")
    extra = next(p for p in session_service.get_participants(db, session.id) if p.role == "ai")
    session_service.remove_seat(db, session.id, extra.seat_order, "host-tok")
    with pytest.raises(ValueError, match="房主自己|至少要保留"):
        session_service.remove_seat(db, session.id, only_seat.seat_order, "host-tok")
    with pytest.raises(ValueError, match="只有房主"):
        session_service.add_seat(db, session.id, "ai", "someone-else")
    for _ in range(session_service.MAX_PLAYER_SEATS - 1):
        session_service.add_seat(db, session.id, "ai", "host-tok")
    with pytest.raises(ValueError, match="最多"):
        session_service.add_seat(db, session.id, "ai", "host-tok")


def test_给AI席指派角色(db_factory):
    db = db_factory()
    module, host, ally = _seed(db)
    session = _room(db, module, host)
    session_service.add_seat(db, session.id, "ai", "host-tok")
    ai_seat = next(p for p in session_service.get_participants(db, session.id) if p.role == "ai")

    session_service.assign_seat_character(db, session.id, ai_seat.seat_order, ally.id, "host-tok")
    assert next(
        p for p in session_service.get_participants(db, session.id) if p.role == "ai"
    ).character_id == ally.id
    assert session_service.lobby_gaps(db, session.id) == []    # 都入座了 → 可开局

    # 真人席不能这样指派：那条路要校验 token 归属，走 claim
    human_seat = next(
        p for p in session_service.get_participants(db, session.id) if p.role == "human"
    )
    with pytest.raises(ValueError, match="本人认领"):
        session_service.assign_seat_character(
            db, session.id, human_seat.seat_order, ally.id, "host-tok")


def test_开局后不能再动座位(db_factory):
    db = db_factory()
    module, host, _ = _seed(db)
    session = _room(db, module, host)
    session_service.start_game(db, session.id, "host-tok")
    with pytest.raises(ValueError, match="游戏已开始"):
        session_service.add_seat(db, session.id, "ai", "host-tok")
