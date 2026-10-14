"""背景音乐：场景氛围的推导、KP 覆盖的作用域、曲库扫描与文件名防穿越。"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.models.module import Module
from app.models.session import GameSession
from app.models.session_participant import SessionParticipant  # noqa: F401 — 注册建表
from app.services import bgm, turn_effects


@pytest.fixture
def db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'bgm.db'}", connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        yield s


SCENES = [
    {"id": "inn", "title": "旅店", "danger": "calm", "mood": "calm"},
    {"id": "street", "title": "街道", "danger": "calm"},
    {"id": "cellar", "title": "地窖", "danger": "uneasy",
     "states": [{"when": ["cellar_flooded"], "danger": "deadly"}]},
    {"id": "altar", "title": "祭坛", "danger": "dangerous",
     "states": [{"when": ["ritual_begun"], "mood": "mythos"}]},
    {"id": "odd", "title": "怪值", "mood": "不安"},
]


def _seed(db, world_state=None):
    module = Module(title="配乐测试", rule_system="coc", scenes=SCENES, npcs=[], clues=[], triggers=[])
    db.add(module)
    db.flush()
    session = GameSession(
        module_id=module.id, status="active", current_scene_id="street",
        world_state=world_state or {},
    )
    db.add(session)
    db.commit()
    return module, session


# ── 场景默认氛围 ──────────────────────────────────────────────────────────


def test_场景显式标注的氛围优先(db):
    module, session = _seed(db)
    assert bgm.scene_mood(module, session, "inn") == "calm"


def test_没标氛围就按危险度推(db):
    module, session = _seed(db)
    # calm 危险度推成「调查」而不是「安宁」：安宁要显式标出来，不是默认值。
    assert bgm.scene_mood(module, session, "street") == "explore"
    assert bgm.scene_mood(module, session, "cellar") == "tense"
    assert bgm.scene_mood(module, session, "altar") == "horror"


def test_剧情flag命中的变体会改变氛围(db):
    module, session = _seed(db, {"flags": {"cellar_flooded": True, "ritual_begun": True}})
    assert bgm.scene_mood(module, session, "cellar") == "horror"   # 变体改了 danger
    assert bgm.scene_mood(module, session, "altar") == "mythos"    # 变体直接给 mood


def test_中文氛围名也认(db):
    module, session = _seed(db)
    assert bgm.scene_mood(module, session, "odd") == "tense"


def test_未知场景回落到默认氛围(db):
    module, session = _seed(db)
    assert bgm.scene_mood(module, session, "nowhere") == bgm.DEFAULT_MOOD
    assert bgm.scene_mood(module, session, None) == bgm.DEFAULT_MOOD


# ── KP 覆盖 ──────────────────────────────────────────────────────────────


def test_覆盖只作用于下达时的场景(db):
    module, session = _seed(db)
    chunks, note = turn_effects.exec_set_mood(db, session.id, session, "horror")
    assert note.startswith("ok")
    # 走 map_update 让前端重取地点列表；不落任何聊天事件
    assert [c.type for c in chunks] == ["map_update"]
    assert bgm.scene_mood(module, session, "street") == "horror"
    # 别的场景不受影响——换场景后自然回到那里的默认
    assert bgm.scene_mood(module, session, "inn") == "calm"


def test_auto_撤销覆盖(db):
    module, session = _seed(db)
    turn_effects.exec_set_mood(db, session.id, session, "sorrow")
    turn_effects.exec_set_mood(db, session.id, session, "auto")
    assert bgm.scene_mood(module, session, "street") == "explore"
    assert "bgm_override" not in (session.world_state or {})


@pytest.mark.parametrize("bad", ["combat", "chase", "jazz", ""])
def test_KP不能手动指定战斗追逐或未知氛围(db, bad):
    module, session = _seed(db)
    chunks, note = turn_effects.exec_set_mood(db, session.id, session, bad)
    assert chunks == []
    assert "无效" in note
    assert "bgm_override" not in (session.world_state or {})


# ── 曲库 ──────────────────────────────────────────────────────────────────


@pytest.fixture
def libs(tmp_path, monkeypatch):
    roots = {"builtin": tmp_path / "builtin", "user": tmp_path / "user"}
    monkeypatch.setattr(bgm, "library_root", lambda source: roots[source])
    return roots


def _put(root, mood, name):
    d = root / mood
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_bytes(b"ID3")


def test_按目录扫描曲库_用户曲目整组替换内置(libs):
    _put(libs["builtin"], "explore", "a.mp3")
    _put(libs["builtin"], "combat", "b.ogg")
    _put(libs["user"], "combat", "我的战斗曲.mp3")
    _put(libs["builtin"], "explore", "notes.txt")        # 非音频不收
    _put(libs["builtin"], "unknown_mood", "x.mp3")       # 不认识的氛围目录不收
    tracks = bgm.list_tracks()
    assert tracks["explore"] == ["/api/bgm/builtin/explore/a.mp3"]
    assert tracks["combat"] == [
        "/api/bgm/user/combat/%E6%88%91%E7%9A%84%E6%88%98%E6%96%97%E6%9B%B2.mp3",
    ]
    assert set(tracks) == {"explore", "combat"}


def test_曲目路径防穿越(libs):
    _put(libs["builtin"], "explore", "a.mp3")
    assert bgm.track_path("builtin", "explore", "a.mp3") is not None
    assert bgm.track_path("builtin", "explore", "../explore/a.mp3") is None
    assert bgm.track_path("builtin", "../builtin", "a.mp3") is None
    assert bgm.track_path("elsewhere", "explore", "a.mp3") is None
    assert bgm.track_path("builtin", "explore", "a.exe") is None
    assert bgm.track_path("builtin", "explore", "missing.mp3") is None
