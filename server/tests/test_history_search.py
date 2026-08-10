"""本局历史检索：片段取哪一段、排序、分页。"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Character, GameSession, Module
from app.models.event_log import EventLog
from app.services import session_service


@pytest.fixture
def db_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'search.db'}", connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@pytest.fixture
def session(db_factory):
    db = db_factory()
    module = Module(title="闇暗山", rule_system="coc")
    hero = Character(name="沃什·帕杉德", rule_system="coc", is_player=True)
    db.add_all([module, hero])
    db.commit()
    gs = GameSession(module_id=module.id, player_character_id=hero.id, status="active")
    db.add(gs)
    db.commit()
    return db, gs


def _add(db, sid, seq, content, etype="narration"):
    """直接建事件（自带 seq），检索只关心 sequence_num 的先后。"""
    ev = EventLog(
        session_id=sid, sequence_num=seq, event_type=etype,
        content=content, actor_name="KP", metadata_={},
    )
    db.add(ev)
    db.commit()
    return ev


# ── 片段取哪一段 ────────────────────────────────────────────────────────


def test_片段以命中处为中心_而不是取开头():
    """线上真实困惑：检索「地藏」，结果里有几条看不到这两个字。

    那几条的正文两三百字，「地藏」落在第 161、255 个字符上，而接口返回的是
    正文前 140 字——匹配在全文上做，展示却只给开头，切掉的正是关键词本身。
    """
    text = "月光照在他苍白的侧脸上，" * 15 + "四周只有他和那尊低垂的地藏，安静地立在路旁。"
    assert text.find("地藏") > 160                      # 前提：关键词在后半截

    snippet = session_service.search_snippet(text, "地藏")
    assert "地藏" in snippet
    assert len(snippet) <= session_service.SNIPPET_CHARS + 2   # 两端各留一个省略号
    assert snippet.startswith("…")                     # 左边有被截掉的上文


def test_命中在开头时不加左省略号():
    text = "地藏石像歪斜地立在那儿，苔藓爬满肩头。" + "低垂的头。" * 60
    snippet = session_service.search_snippet(text, "地藏")
    assert snippet.startswith("地藏")
    assert snippet.endswith("…")


def test_短内容原样返回_不加省略号():
    text = "你放慢脚步，在地藏像前蹲下来。"
    assert session_service.search_snippet(text, "地藏") == text


def test_命中在末尾时窗口向左推满():
    """否则片段会缩成只剩关键词那几个字，等于没给上下文。"""
    text = "夜风把落叶吹得沙沙响。" * 30 + "地藏"
    snippet = session_service.search_snippet(text, "地藏")
    assert snippet.endswith("地藏")
    assert len(snippet) >= session_service.SNIPPET_CHARS   # 左边补足了上文


def test_查不到关键词时退回取开头():
    """大小写/空白差异导致片段定位失败也不能炸，退回旧行为即可。"""
    text = "长廊尽头的烛火忽明忽暗。" * 30
    snippet = session_service.search_snippet(text, "并不存在的词")
    assert snippet.startswith("长廊尽头") and snippet.endswith("…")


def test_大小写不敏感():
    text = "he opened the door. " * 20 + "The Idol stood there."
    assert "Idol" in session_service.search_snippet(text, "idol")


# ── 排序与分页 ──────────────────────────────────────────────────────────


def test_默认由新到旧(session):
    db, gs = session
    for i in range(1, 6):
        _add(db, gs.id, i, f"第{i}段：地藏石像立在路旁。")
    rows, total = session_service.search_events(db, gs.id, "地藏")
    assert total == 5
    assert [e.sequence_num for e in rows] == [5, 4, 3, 2, 1]


def test_可切换为由旧到新(session):
    db, gs = session
    for i in range(1, 6):
        _add(db, gs.id, i, f"第{i}段：地藏石像立在路旁。")
    rows, _ = session_service.search_events(db, gs.id, "地藏", order="asc")
    assert [e.sequence_num for e in rows] == [1, 2, 3, 4, 5]


def test_分页返回本页与总数(session):
    """总数是分页控件的前提——没有它，用户不知道自己在多大的结果集里翻。"""
    db, gs = session
    for i in range(1, 13):
        _add(db, gs.id, i, f"第{i}段：地藏石像立在路旁。")

    page1, total = session_service.search_events(db, gs.id, "地藏", limit=5, offset=0)
    page2, _ = session_service.search_events(db, gs.id, "地藏", limit=5, offset=5)
    page3, _ = session_service.search_events(db, gs.id, "地藏", limit=5, offset=10)

    assert total == 12
    assert [e.sequence_num for e in page1] == [12, 11, 10, 9, 8]
    assert [e.sequence_num for e in page2] == [7, 6, 5, 4, 3]
    assert [e.sequence_num for e in page3] == [2, 1]
    # 页与页之间不重不漏
    seqs = [e.sequence_num for e in page1 + page2 + page3]
    assert sorted(seqs, reverse=True) == list(range(12, 0, -1))


def test_空查询返回空(session):
    db, gs = session
    _add(db, gs.id, 1, "地藏石像。")
    assert session_service.search_events(db, gs.id, "  ") == ([], 0)


def test_越界的分页参数不炸(session):
    db, gs = session
    _add(db, gs.id, 1, "地藏石像。")
    rows, total = session_service.search_events(db, gs.id, "地藏", limit=0, offset=-5)
    assert total == 1 and len(rows) == 1
