"""确定性进场检定守卫：模组写明「进入本场景时投X」→ 引擎补发，不靠 KP（开场更是禁发检定）。"""

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Character, GameSession, Module, SessionParticipant  # noqa: F401
from app.services import chat_service as cs
from app.services import planned_effects, session_service

# 取自「常暗之箱」6 号车厢：第一条是进场全员幸运，第二条要玩家先发现地图才投灵感。
_SCENE_6 = {
    "id": "scene_6",
    "title": "6号车厢",
    "events": [
        {
            "trigger": "进入6号车厢时",
            "kind": "dice_check",
            "skill": "幸运",
            "note": "全员幸运检定，失败则失去随身物品，只保留衣服和眼镜等。",
        },
        {
            "trigger": "发现门旁有电车示意地图（可选择用《侦查》发现），进行《灵感》鉴定",
            "kind": "dice_check",
            "skill": "灵感",
            "note": "成功：得知7号车厢以后的地方被蓄意涂掉。",
        },
    ],
}
_SCENE_5 = {
    "id": "scene_5",
    "title": "5号车厢",
    "events": [{"trigger": "阅读报纸时", "kind": "san_check", "san_loss": "0/1"}],
}


@pytest.fixture
def db_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'entry.db'}", connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed(db, scenes=None, scene_id="scene_6"):
    module = Module(title="常暗之箱", rule_system="coc", npcs=[],
                    scenes=scenes if scenes is not None else [_SCENE_6, _SCENE_5])
    chars = [
        Character(name=name, rule_system="coc", is_player=True,
                  base_attributes={"LUCK": 50}, skills={},
                  system_data={"sanity": {"current": 60, "max": 99}})
        for name in ("江户川龙牙", "林知微", "相马直树")
    ]
    db.add_all([module, *chars]); db.flush()
    s = GameSession(module_id=module.id, player_character_id=chars[0].id,
                    status="active", current_scene_id=scene_id, world_state={})
    db.add(s); db.commit()
    return s.id, chars[0], chars[1:], module


def _run(coro):
    async def collect():
        return [c async for c in coro]
    return asyncio.run(collect())


def _guard(db, sid, pc, mates, module):
    return _run(cs._ensure_scene_entry_checks(
        db, sid, db.get(GameSession, sid), module, pc, mates,
    ))


def _dice_events(db, sid):
    """本局已发出的检定：真人挂 check_request、AI 席直接落 dice 事件。"""
    out = []
    for ev in session_service.get_session_events(db, sid):
        meta = ev.metadata_ or {}
        if meta.get("check_request") or ev.event_type == "dice":
            out.append((ev, meta))
    return out


# ── 机制点识别 ──

def test_only_entry_flavored_dice_checks_are_picked():
    module = Module(title="M", rule_system="coc", scenes=[_SCENE_6])
    found = planned_effects._scene_entry_check_mechanisms(module, "scene_6")
    assert [index for index, _ in found] == [0]          # 「发现地图后投灵感」不算进场
    assert found[0][1]["skill"] == "幸运"


def test_sanity_mechanisms_are_not_picked_as_dice_checks():
    module = Module(title="M", rule_system="coc", scenes=[_SCENE_5])
    assert planned_effects._scene_entry_check_mechanisms(module, "scene_5") == []


def test_travel_flavored_trigger_counts_as_entry():
    scene = {"id": "s7", "events": [
        {"trigger": "前往7号车厢时", "kind": "dice_check", "skill": "灵感"},
    ]}
    module = Module(title="M", rule_system="coc", scenes=[scene])
    assert len(planned_effects._scene_entry_check_mechanisms(module, "s7")) == 1


# ── 守卫行为 ──

def test_guard_fires_entry_check_for_whole_party(db_factory):
    """开场三人同处 6 号车厢 → 三人各得一次幸运检定（这正是过去整条漏掉的机制）。"""
    db = db_factory(); sid, pc, mates, module = _seed(db)
    assert _guard(db, sid, pc, mates, module)
    rolled = _dice_events(db, sid)
    names = {meta.get("actor_name") or ev.content.split("｜")[0] for ev, meta in rolled}
    assert {"江户川龙牙", "林知微", "相马直树"} <= names
    # 只发进场那条，「发现地图后投灵感」不能被顺手带出来
    assert all("灵感" not in (ev.content or "") for ev, _ in rolled)


def test_guard_is_idempotent(db_factory):
    db = db_factory(); sid, pc, mates, module = _seed(db)
    assert _guard(db, sid, pc, mates, module)
    before = len(_dice_events(db, sid))
    assert _guard(db, sid, pc, mates, module) == []      # 第二轮不再重复发
    assert len(_dice_events(db, sid)) == before


def test_guard_records_idempotency_keys_per_character(db_factory):
    db = db_factory(); sid, pc, mates, module = _seed(db)
    _guard(db, sid, pc, mates, module)
    ws = db.get(GameSession, sid).world_state or {}
    keys = set(ws.get("scene_entry_checks") or [])
    assert keys == {f"scene:scene_6:check:0:{c.id}" for c in [pc, *mates]}


def test_guard_preserves_pending_checks_written_by_dice_exec(db_factory):
    """记账不能用旧快照覆盖 world_state——待玩家投骰的 pending 必须还在。"""
    db = db_factory(); sid, pc, mates, module = _seed(db)
    _guard(db, sid, pc, mates, module)
    ws = db.get(GameSession, sid).world_state or {}
    assert ws.get("pending_checks")
    assert ws.get("scene_entry_checks")


def test_guard_noop_without_entry_mechanism(db_factory):
    db = db_factory(); sid, pc, mates, module = _seed(db, scenes=[_SCENE_5], scene_id="scene_5")
    assert _guard(db, sid, pc, mates, module) == []


def test_guard_only_covers_characters_in_that_scene(db_factory):
    """分头行动：留在别处的队友不参加这里的进场检定，等他自己到了再补。"""
    db = db_factory(); sid, pc, mates, module = _seed(db)
    session_service.set_char_location(db, sid, mates[1].id, "scene_5")
    _guard(db, sid, pc, mates, module)
    keys = set((db.get(GameSession, sid).world_state or {}).get("scene_entry_checks") or [])
    assert f"scene:scene_6:check:0:{mates[1].id}" not in keys
    assert f"scene:scene_6:check:0:{pc.id}" in keys

    # 该队友后来走进 6 号车厢 → 这时才补上他那一份
    session_service.set_char_location(db, sid, mates[1].id, "scene_6")
    assert _guard(db, sid, pc, mates, module)
    keys = set((db.get(GameSession, sid).world_state or {}).get("scene_entry_checks") or [])
    assert f"scene:scene_6:check:0:{mates[1].id}" in keys
