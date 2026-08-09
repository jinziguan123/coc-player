"""NPC 对外称呼：玩家还没认出来的东西，机制界面不能替 KP 把名字说出来。"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Character, GameSession, Module
from app.services import npc_identity as ni
from app.services import session_service


@pytest.fixture
def db_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'ident.db'}", connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


MONSTER = "田间潜随者（莎布·尼古拉丝化身）"
STUDENT = "香澄澪"


def _seed(db):
    module = Module(
        title="闇暗山",
        rule_system="coc",
        scenes=[{"id": "s1", "name": "土路"}],
        npcs=[
            {"id": "npc_student", "name": STUDENT, "description": "学生服青年"},
            {"id": "npc_field", "name": MONSTER, "description": "山顶枯树上现身的神"},
            {"id": "npc_yobuko", "name": "呼子（蠕虫行者）", "unknown_as": "林中的声音"},
        ],
        clues=[],
        world_setting={},
    )
    hero = Character(name="沃什·帕杉德", rule_system="coc", is_player=True)
    db.add_all([module, hero])
    db.commit()
    session = GameSession(
        module_id=module.id, player_character_id=hero.id,
        status="active", current_scene_id="s1", world_state={},
    )
    db.add(session)
    db.commit()
    return module, hero, session


def _narrate(db, sid, text, **kw):
    return session_service.add_event(db, sid, "narration", text, actor_name="KP", **kw)


def test_没在叙事里出现过的名字一律遮住(db_factory):
    """截图里的那一幕：叙事只写「一团比夜色更浓的黑」，
    对抗检定卡却印出「田间潜随者（莎布·尼古拉丝化身）」，玩家一眼就知道对面是谁。"""
    db = db_factory()
    module, _hero, session = _seed(db)
    _narrate(db, session.id, "树影边缘，一团比夜色更浓的黑正从土路中央隆起。没有脸，没有眼。")

    mask = ni.build_masker(db, session.id, module)
    assert mask(MONSTER) == ni.UNKNOWN_LABEL
    assert mask(STUDENT) == ni.UNKNOWN_LABEL


def test_模组可以给更贴切的中性称呼(db_factory):
    db = db_factory()
    module, _hero, session = _seed(db)
    mask = ni.build_masker(db, session.id, module)
    assert mask("呼子（蠕虫行者）") == "林中的声音"


def test_叙事里写过外号就显示外号_但神话身份仍然遮着(db_factory):
    """KP 说破「田间潜随者」这个称呼时玩家就认识它了；
    但「莎布·尼古拉丝化身」是另一层——那是知道自己在跟哪尊旧日支配者打交道。"""
    db = db_factory()
    module, _hero, session = _seed(db)
    _narrate(db, session.id, "山民管这种东西叫田间潜随者。")

    mask = ni.build_masker(db, session.id, module)
    assert mask(MONSTER) == "田间潜随者"
    assert "莎布" not in mask(MONSTER)


def test_神话身份出现后给全名(db_factory):
    """知道了它是谁的化身，再遮外号没有意义。"""
    db = db_factory()
    module, _hero, session = _seed(db)
    _narrate(db, session.id, "书页上写着：那是莎布·尼古拉丝化身，千仔之母的一枝。")

    mask = ni.build_masker(db, session.id, module)
    assert mask(MONSTER) == MONSTER


def test_NPC自报家门当场改口(db_factory):
    """否则气泡会是「不明存在：我叫香澄澪」——同一条消息里自己打自己的脸。"""
    db = db_factory()
    module, _hero, session = _seed(db)

    assert ni.build_masker(db, session.id, module)(STUDENT) == ni.UNKNOWN_LABEL
    mask = ni.build_masker(db, session.id, module)
    assert mask(STUDENT, extra_prose="我叫香澄澪，是这附近的学生。") == STUDENT


def test_对白正文也算认识途径(db_factory):
    db = db_factory()
    module, _hero, session = _seed(db)
    session_service.add_event(
        db, session.id, "dialogue", "我叫香澄澪。", actor_name="某人",
    )
    assert ni.build_masker(db, session.id, module)(STUDENT) == STUDENT


def test_仅KP可见的事件不算玩家知道(db_factory):
    """幕后推演里 KP 自己盘算过的东西，玩家没看见。"""
    db = db_factory()
    module, _hero, session = _seed(db)
    _narrate(
        db, session.id, "（幕后）田间潜随者已经盯上了调查员。",
        visibility=[session_service.KP_ONLY_SENTINEL],
    )
    assert ni.build_masker(db, session.id, module)(MONSTER) == ni.UNKNOWN_LABEL


def test_机制事件不算认识途径(db_factory):
    """否则会自我实现：检定卡印一次名字，就永远算「玩家已知」，遮罩当场失效。"""
    db = db_factory()
    module, _hero, session = _seed(db)
    session_service.add_event(
        db, session.id, "dice", f"对抗骰　沃什·帕杉德 vs {MONSTER}", actor_name="系统",
    )
    assert ni.build_masker(db, session.id, module)(MONSTER) == ni.UNKNOWN_LABEL


def test_不是本模组NPC的名字原样通过(db_factory):
    """玩家角色、临场 NPC 不该被误伤。"""
    db = db_factory()
    module, _hero, session = _seed(db)
    mask = ni.build_masker(db, session.id, module)
    assert mask("沃什·帕杉德") == "沃什·帕杉德"
    assert mask("卖报的老头") == "卖报的老头"
    assert mask("") == ""
    assert mask(None) == ""


def test_没有模组时不遮任何东西(db_factory):
    db = db_factory()
    _module, _hero, session = _seed(db)
    mask = ni.build_masker(db, session.id, None)
    assert mask(MONSTER) == MONSTER


def test_reveals_报告是否照实显示(db_factory):
    db = db_factory()
    module, _hero, session = _seed(db)
    mask = ni.build_masker(db, session.id, module)
    assert mask.reveals("沃什·帕杉德") is True
    assert mask.reveals(MONSTER) is False


@pytest.mark.parametrize("raw,expected", [
    ("田间潜随者（莎布·尼古拉丝化身）", ("田间潜随者", "莎布·尼古拉丝化身")),
    ("呼子(蠕虫行者)", ("呼子", "蠕虫行者")),
    ("香澄澪", ("香澄澪", "")),
    ("", ("", "")),
    # 括号不在结尾的不算 KP 侧注释，别把人家名字拆了
    ("（代号）K先生", ("（代号）K先生", "")),
])
def test_拆名(raw, expected):
    assert ni.split_name(raw) == expected
