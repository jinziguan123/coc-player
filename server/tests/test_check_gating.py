"""检定门控收益：暂存只属于发起回合；自动掷（暗投/AI 队友）同样兑现。"""
import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.ai.turn_planner import TurnPlan
from app.models import Base, Character, GameSession, Module  # noqa: F401
from app.services import check_gating, planned_effects, session_service, turn_effects


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'g.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _seed(db):
    module = Module(
        title="m", rule_system="coc", npcs=[], scenes=[{"id": "study", "title": "书房"}],
        clues=[{"id": "clue_letter", "name": "密信"}],
    )
    pc = Character(name="亨利", rule_system="coc", is_player=True, skills={"侦查": 50, "心理学": 50},
                   system_data={"hitPoints": {"current": 10, "max": 10}})
    db.add_all([module, pc]); db.flush()
    s = GameSession(module_id=module.id, player_character_id=pc.id, status="active",
                    current_scene_id="study", world_state={})
    db.add(s); db.commit()
    session_service.add_event(db, s.id, "action", "我翻找书桌", actor_id=pc.id)
    return s, pc, module


def _fix_roll(monkeypatch, value):
    monkeypatch.setattr("app.rules.coc.components.check.roll_percentile", lambda: value)


def _plan(**kw):
    return TurnPlan(
        requires_check=True, check={"skill": "侦查"},
        items_gained=[{"name": "黄铜钥匙", "who": "亨利"}], **kw,
    )


def _stash_items(db, s, pc, plan):
    async def run():
        return [c async for c in planned_effects._ensure_planned_items(db, s.id, s, pc, [], plan)]
    return asyncio.run(run())


def _blind_check(db, s, pc, module, skill="侦查"):
    return asyncio.run(turn_effects._exec_dice_check(
        db, s.id, s, module, {"skill": skill, "visibility": "blind"}, pc, [],
    ))


@pytest.mark.parametrize("roll,expected", [(10, True), (95, False)])
def test_暗投在物品暂存之后结算时兑现或作废(db, monkeypatch, roll, expected):
    s, pc, module = _seed(db)
    _stash_items(db, s, pc, _plan())
    assert s.turn_state["pending_item_gains"][0]["skill"] == "侦查"
    _fix_roll(monkeypatch, roll)
    _blind_check(db, s, pc, module)
    db.expire_all()
    s = db.get(GameSession, s.id)
    assert s.turn_state["pending_item_gains"] == []
    gained = [e for e in session_service.get_session_events(db, s.id) if (e.metadata_ or {}).get("item_gain")]
    assert bool(gained) is expected


@pytest.mark.parametrize("roll,expected", [(10, True), (95, False)])
def test_暗投先于叙事后物品守卫时按已知成败直接结算(db, monkeypatch, roll, expected):
    s, pc, module = _seed(db)
    _fix_roll(monkeypatch, roll)
    _blind_check(db, s, pc, module)
    _stash_items(db, s, pc, _plan())
    db.expire_all()
    s = db.get(GameSession, s.id)
    assert not (s.turn_state or {}).get("pending_item_gains")
    gained = [e for e in session_service.get_session_events(db, s.id) if (e.metadata_ or {}).get("item_gain")]
    assert bool(gained) is expected


def test_不相干技能的自动掷不兑现暂存(db, monkeypatch):
    s, pc, module = _seed(db)
    _stash_items(db, s, pc, _plan())
    _fix_roll(monkeypatch, 10)
    _blind_check(db, s, pc, module, skill="心理学")
    db.expire_all()
    assert db.get(GameSession, s.id).turn_state["pending_item_gains"]


def test_暗投兑现同技能的线索暂存(db, monkeypatch):
    from app.services import world_memory
    s, pc, module = _seed(db)
    s.world_state = world_memory.stage_clue_reveal({}, ["clue_letter"], [pc.id], 1, skill="侦查")
    db.commit()
    _fix_roll(monkeypatch, 10)
    _blind_check(db, s, pc, module)
    db.expire_all()
    ws = db.get(GameSession, s.id).world_state
    assert "pending_clue_reveals" not in ws
    assert ws["clue_ledger"]["clue_letter"]["status"] == "known"


def test_新回合作废上一轮暂存_旧暂存不被之后的投骰兑现(db):
    from app.services import world_memory
    s, pc, _module = _seed(db)
    _stash_items(db, s, pc, _plan())
    s.world_state = world_memory.stage_clue_reveal({}, ["clue_letter"], [pc.id], 1, skill="侦查")
    db.commit()
    assert check_gating.begin_turn(db, s)
    db.expire_all()
    s = db.get(GameSession, s.id)
    assert not s.turn_state.get("pending_item_gains")
    assert "pending_clue_reveals" not in (s.world_state or {})
    planned_effects.settle_pending_clue_reveals(db, s.id, s, True)
    assert not (db.get(GameSession, s.id).world_state or {}).get("clue_ledger")


def test_重生成保留已掷骰子_已知成败随之保留(db, monkeypatch):
    """回滚 KP 输出刻意保留已投出的骰子（不重掷），自动检定的成败也就继续有效。"""
    s, pc, module = _seed(db)
    _fix_roll(monkeypatch, 10)
    _blind_check(db, s, pc, module)
    assert check_gating.auto_outcome(db, s, "侦查") is True
    session_service.add_event(db, s.id, "narration", "你翻到了东西。", actor_name="KP")
    session_service.rollback_last_kp_output(db, s.id)
    db.expire_all()
    assert check_gating.auto_outcome(db, db.get(GameSession, s.id), "侦查") is True
    # 骰子事件真被删掉（如人工清理）时不再沿用其成败
    from app.models.event_log import EventLog
    db.query(EventLog).filter(EventLog.event_type == "dice").delete()
    db.commit()
    db.expire_all()
    assert check_gating.auto_outcome(db, db.get(GameSession, s.id), "侦查") is None


# ── 物品效果通道：库存、事件、台账键同一事务 ──────────────────────────────


def _inventory(db, pc):
    db.expire_all()
    return [(i["name"], i.get("qty")) for i in (db.get(Character, pc.id).system_data or {}).get("inventory") or []]


def _gain_plan():
    return TurnPlan(items_gained=[{"name": "黄铜钥匙", "who": "亨利"}])


def test_物品获得中途失败整笔回滚_重试只发一次(db, monkeypatch):
    s, pc, _module = _seed(db)
    real = session_service.add_event

    def boom(*a, **k):
        raise RuntimeError("事件写入失败")

    monkeypatch.setattr(session_service, "add_event", boom)
    import pytest as _pytest
    with _pytest.raises(RuntimeError):
        _stash_items(db, s, pc, _gain_plan())
    monkeypatch.setattr(session_service, "add_event", real)
    db.expire_all()
    s = db.get(GameSession, s.id)
    assert _inventory(db, pc) == []
    assert not (s.turn_state or {}).get("item_delta_keys")
    _stash_items(db, s, pc, _gain_plan())
    _stash_items(db, s, pc, _gain_plan())   # 重新生成再跑：按台账键幂等
    assert _inventory(db, pc) == [("黄铜钥匙", 1)]
    gains = [e for e in session_service.get_session_events(db, s.id) if (e.metadata_ or {}).get("item_gain")]
    assert len(gains) == 1 and gains[0].metadata_["effect"]["kind"] == "item_gain"


def test_失去不存在的物品也记键_不反复尝试(db):
    s, pc, _module = _seed(db)
    plan = TurnPlan(items_lost=[{"name": "火柴", "who": "亨利"}])
    assert [c for c in _stash_items(db, s, pc, plan) if c.type == "system"] == []
    db.expire_all()
    assert any(k.startswith("l|") for k in db.get(GameSession, s.id).turn_state["item_delta_keys"])


def test_每类效果载荷都在权威表登记():
    from app.services import effect_channel
    payloads = [effect_channel.HpChange, effect_channel.ItemChange,
                effect_channel.FlagChange, effect_channel.SanChange]
    assert set(effect_channel.AUTHORITY) == set(payloads)
    for cls in payloads:
        entry = effect_channel.AUTHORITY[cls]
        assert entry.sources and entry.dedupe and entry.on_regenerate


def test_仅移动状态也报告变更且只提交一次(db, monkeypatch):
    from app.services import movement
    s, _, _ = _seed(db)
    s.turn_state = {movement.MOVES_KEY: [{"status": "blocked"}], "keep": {"x": 1}}
    db.commit()
    commits = []
    original = db.commit
    def commit():
        commits.append(True)
        original()
    monkeypatch.setattr(db, "commit", commit)
    assert check_gating.begin_turn(db, s) is True
    assert commits == [True]
    db.refresh(s)
    assert s.turn_state == {"keep": {"x": 1}}
    assert check_gating.begin_turn(db, s) is False
    assert commits == [True]


def test_新回合清理中途失败回滚所有已改状态(db, monkeypatch):
    from app.services import auto_check_state, movement
    s, _, _ = _seed(db)
    state = {movement.MOVES_KEY: [{"status": "pending_check"}],
             "pending_item_gains": [{"name": "钥匙"}], "auto_checks": [{"event_id": "e"}]}
    s.turn_state = state
    s.world_state = {"pending_clue_reveals": {"ids": ["c"]}, "keep": True}
    db.commit()
    def fail(_session):
        raise RuntimeError("清理失败")
    monkeypatch.setattr(auto_check_state, "clear", fail)
    with pytest.raises(RuntimeError, match="清理失败"):
        check_gating.begin_turn(db, s)
    db.refresh(s)
    assert s.turn_state == state
    assert s.world_state == {"pending_clue_reveals": {"ids": ["c"]}, "keep": True}
