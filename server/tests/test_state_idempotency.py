"""状态变更幂等：「重新生成」回滚本轮 KP 判的扣血；SAN 按本轮锚点 + 回滚 credit 去重。"""

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.ai.turn_planner import MishapPolicy, TurnPlan
from app.models import Base, Character, GameSession, Module  # noqa: F401
from app.services import chat_service as cs
from app.services import session_service, turn_effects
from tests.wire import wires


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'r.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _seed(db):
    module = Module(title="m", rule_system="coc", npcs=[], scenes=[])
    pc = Character(name="亨利", rule_system="coc", is_player=True, base_attributes={"CON": 60},
                   system_data={"hitPoints": {"current": 15, "max": 15}})
    mate = Character(name="阿尔法", rule_system="coc", is_player=False, base_attributes={"POW": 50},
                     system_data={"hitPoints": {"current": 12, "max": 12},
                                  "sanity": {"current": 50, "max": 99}})
    db.add_all([module, pc, mate]); db.flush()
    s = GameSession(module_id=module.id, player_character_id=pc.id, status="active", world_state={})
    db.add(s); db.commit()
    return s, pc, mate, module


def _hp(db, cid):
    db.expire_all()
    return db.get(Character, cid).system_data["hitPoints"]["current"]


def _san(db, cid):
    db.expire_all()
    return db.get(Character, cid).system_data["sanity"]["current"]


def test_重新生成后KP再发同一hp_change_伤害叠加两次(db):
    s, pc, _m, module = _seed(db)
    session_service.add_event(db, s.id, "action", "我踢开燃烧瓶", actor_id=pc.id, actor_name=pc.name)
    # 第一次生成：KP 工具循环里 hp_change -4，然后写旁白
    asyncio.run(turn_effects._exec_hp_change(db, s.id, pc, "player", "-4", "火焰烧伤", module=module))
    session_service.add_event(db, s.id, "narration", "火舌舔上你的裤腿。", actor_name="KP")
    assert _hp(db, pc.id) == 11
    # 玩家点「重新生成」：回滚按撤销台账把这处扣血一并撤掉
    session_service.rollback_last_kp_output(db, s.id)
    assert _hp(db, pc.id) == 15
    assert not [e for e in session_service.get_session_events(db, s.id) if (e.metadata_ or {}).get('hp_change')]
    # 重新生成的 KP 再判同一处伤害 → 只扣这一次
    asyncio.run(turn_effects._exec_hp_change(db, s.id, pc, "player", "-4", "火焰烧伤", module=module))
    assert _hp(db, pc.id) == 11


def test_重新生成后大失败反噬守卫再扣一次(db):
    s, pc, _m, module = _seed(db)
    session_service.add_event(db, s.id, "action", "我踢开燃烧瓶", actor_id=pc.id, actor_name=pc.name)
    plan = TurnPlan(mishap=MishapPolicy(trigger=True, hp_delta=-3, reason="火焰溅到腿上"))

    async def collect(gen):
        return wires([c async for c in gen])

    pre = session_service.get_next_sequence_num(db, s.id) - 1
    asyncio.run(collect(cs._ensure_planned_mishap(db, s.id, pc, [], plan, pre)))
    session_service.add_event(db, s.id, "narration", "……", actor_name="KP")
    assert _hp(db, pc.id) == 12
    session_service.rollback_last_kp_output(db, s.id)
    assert _hp(db, pc.id) == 15     # 回滚撤销了守卫的扣血
    pre = session_service.get_next_sequence_num(db, s.id) - 1
    asyncio.run(collect(cs._ensure_planned_mishap(db, s.id, pc, [], plan, pre)))
    assert _hp(db, pc.id) == 12


def test_同一工具循环内重复调用hp_change_叠加(db):
    s, pc, _m, module = _seed(db)
    for _ in range(2):
        asyncio.run(turn_effects._exec_hp_change(db, s.id, pc, "player", "-2", "被抓伤", module=module))
    assert _hp(db, pc.id) == 11     # 刻意不去重：同一轮挨两下同样的抓伤是合法的


def test_无source的san_check重新生成后不重复扣SAN(db):
    s, pc, mate, _module = _seed(db)
    session_service.add_event(db, s.id, "action", "我掀开床单", actor_id=pc.id, actor_name=pc.name)
    kv = {"chars": "阿尔法", "success_loss": "3", "failure_loss": "3"}
    asyncio.run(turn_effects._exec_san_check(db, s.id, s, kv, pc, [mate]))
    assert _san(db, mate.id) == 47
    session_service.rollback_last_kp_output(db, s.id)
    asyncio.run(turn_effects._exec_san_check(db, s.id, s, kv, pc, [mate]))
    assert _san(db, mate.id) == 47  # 本轮锚点顶上幂等键


def test_有source的san_check重新生成后不重复(db):
    s, pc, mate, _module = _seed(db)
    session_service.add_event(db, s.id, "action", "我掀开床单", actor_id=pc.id, actor_name=pc.name)
    kv = {"chars": "阿尔法", "success_loss": "3", "failure_loss": "3", "source": "床下的尸体"}
    asyncio.run(turn_effects._exec_san_check(db, s.id, s, kv, pc, [mate]))
    session_service.rollback_last_kp_output(db, s.id)
    asyncio.run(turn_effects._exec_san_check(db, s.id, s, kv, pc, [mate]))
    assert _san(db, mate.id) == 47  # 对照组：有 source 时账本去重生效
    # 再重新生成一次、KP 换了说法：回滚留下的 credit 抵掉，不再扣
    session_service.rollback_last_kp_output(db, s.id)
    kv2 = {**kv, "source": "床底那具尸体"}
    asyncio.run(turn_effects._exec_san_check(db, s.id, s, kv2, pc, [mate]))
    assert _san(db, mate.id) == 47


def _san_run(db, s, pc, mate, source):
    kv = {"chars": "阿尔法", "success_loss": "3", "failure_loss": "3", "source": source}
    asyncio.run(turn_effects._exec_san_check(db, s.id, s, kv, pc, [mate]))


def test_同一版输出里两个不同恐怖源各扣各的(db):
    s, pc, mate, _module = _seed(db)
    session_service.add_event(db, s.id, "action", "我掀开床单", actor_id=pc.id, actor_name=pc.name)
    _san_run(db, s, pc, mate, "床下的尸体")
    _san_run(db, s, pc, mate, "窗外的触手")
    assert _san(db, mate.id) == 44


def test_重新生成连续换说法都不重复扣(db):
    s, pc, mate, _module = _seed(db)
    session_service.add_event(db, s.id, "action", "我掀开床单", actor_id=pc.id, actor_name=pc.name)
    _san_run(db, s, pc, mate, "床下的尸体")
    for wording in ("床底那具尸体", "那具躺在床下的死人", "腐烂的遗体"):
        session_service.rollback_last_kp_output(db, s.id)
        _san_run(db, s, pc, mate, wording)
    assert _san(db, mate.id) == 47


def test_重新生成沿用原措辞再加一个新恐怖源_新的照扣(db):
    s, pc, mate, _module = _seed(db)
    session_service.add_event(db, s.id, "action", "我掀开床单", actor_id=pc.id, actor_name=pc.name)
    _san_run(db, s, pc, mate, "床下的尸体")
    session_service.rollback_last_kp_output(db, s.id)
    _san_run(db, s, pc, mate, "床下的尸体")   # 账本认出，同时销掉对应 credit
    _san_run(db, s, pc, mate, "窗外的触手")   # 真不同的恐怖源，不能被错抵
    assert _san(db, mate.id) == 44


def test_重新生成的credit不跨轮(db):
    s, pc, mate, _module = _seed(db)
    session_service.add_event(db, s.id, "action", "我掀开床单", actor_id=pc.id, actor_name=pc.name)
    _san_run(db, s, pc, mate, "床下的尸体")
    session_service.rollback_last_kp_output(db, s.id)
    # 重生成的 KP 没再下 SAN，玩家接着行动：上一轮的 credit 作废
    session_service.add_event(db, s.id, "action", "我推开衣柜", actor_id=pc.id, actor_name=pc.name)
    _san_run(db, s, pc, mate, "衣柜里的头颅")
    assert _san(db, mate.id) == 44


def test_credit只抵同一角色(db):
    s, pc, mate, _module = _seed(db)
    sd = dict(pc.system_data); sd["sanity"] = {"current": 50, "max": 99}; pc.system_data = sd; db.commit()
    session_service.add_event(db, s.id, "action", "我掀开床单", actor_id=pc.id, actor_name=pc.name)
    _san_run(db, s, pc, mate, "床下的尸体")          # 只有阿尔法目睹
    session_service.rollback_last_kp_output(db, s.id)
    kv = {"chars": "亨利", "success_loss": "1", "failure_loss": "1d4", "source": "床底那具尸体"}
    _c, _d, pending = asyncio.run(turn_effects._exec_san_check(db, s.id, s, kv, pc, [mate]))
    assert pending   # 亨利没扣过，不能拿阿尔法的 credit 抵


def test_真人已投的san_重新生成换说法不再挂待投(db):
    s, pc, _mate, _module = _seed(db)
    sd = dict(pc.system_data); sd["sanity"] = {"current": 50, "max": 99}; pc.system_data = sd; db.commit()
    session_service.add_event(db, s.id, "action", "我掀开床单", actor_id=pc.id, actor_name=pc.name)
    kv = {"chars": "亨利", "success_loss": "1", "failure_loss": "1", "source": "床下的尸体"}
    asyncio.run(turn_effects._exec_san_check(db, s.id, s, kv, pc, []))
    db.expire_all()
    check = next(iter(db.get(GameSession, s.id).turn_state["pending_checks"].values()))
    # 模拟玩家点击投骰：结算 → 出队 → 记账（同 turn_orchestrator 的待投路径）
    turn_effects._settle_san_target(db, s.id, s, pc, "1", "1", check["san_key"], mark_checked=False)
    session_service.pop_pending_check(db, s.id, check["id"])
    turn_effects._mark_san_checked(db, db.get(GameSession, s.id), check["san_key"], pc.id)
    assert _san(db, pc.id) == 49
    session_service.rollback_last_kp_output(db, s.id)
    kv2 = {**kv, "source": "床底那具尸体"}
    _c, _d, pending = asyncio.run(turn_effects._exec_san_check(db, s.id, s, kv2, pc, []))
    assert not pending
    assert _san(db, pc.id) == 49


def test_回滚复原重伤状态并删掉附属的体质骰(db, monkeypatch):
    monkeypatch.setattr("app.rules.coc.components.check.roll_percentile", lambda: 99)  # 体质必失败
    s, pc, _m, module = _seed(db)
    session_service.add_event(db, s.id, "action", "我冲过去", actor_id=pc.id, actor_name=pc.name)
    asyncio.run(turn_effects._exec_hp_change(db, s.id, pc, "player", "-8", "被砍中", module=module))
    db.expire_all()
    assert db.get(Character, pc.id).status == "unconscious"
    assert any((e.metadata_ or {}).get("major_wound_check")
               for e in session_service.get_session_events(db, s.id))
    session_service.rollback_last_kp_output(db, s.id)
    db.expire_all()
    char = db.get(Character, pc.id)
    assert char.system_data["hitPoints"]["current"] == 15
    assert char.status != "unconscious"
    assert not char.system_data.get("conditions")
    assert not any((e.metadata_ or {}).get("major_wound_check")
                   for e in session_service.get_session_events(db, s.id))


def test_回滚只撤本轮_上一轮的扣血保留(db):
    s, pc, _m, module = _seed(db)
    session_service.add_event(db, s.id, "action", "第一轮", actor_id=pc.id, actor_name=pc.name)
    asyncio.run(turn_effects._exec_hp_change(db, s.id, pc, "player", "-3", "旧伤", module=module))
    session_service.add_event(db, s.id, "action", "第二轮", actor_id=pc.id, actor_name=pc.name)
    asyncio.run(turn_effects._exec_hp_change(db, s.id, pc, "player", "-2", "新伤", module=module))
    asyncio.run(turn_effects._exec_hp_change(db, s.id, pc, "player", "-1", "又一下", module=module))
    session_service.rollback_last_kp_output(db, s.id)
    assert _hp(db, pc.id) == 12


def test_回滚连同急救标记一并复原(db):
    s, pc, _m, module = _seed(db)
    sd = dict(pc.system_data); sd["firstAidUsed"] = True; pc.system_data = sd; db.commit()
    session_service.add_event(db, s.id, "action", "我继续走", actor_id=pc.id, actor_name=pc.name)
    asyncio.run(turn_effects._exec_hp_change(db, s.id, pc, "player", "-2", "绊倒", module=module))
    db.expire_all()
    assert db.get(Character, pc.id).system_data["firstAidUsed"] is False
    session_service.rollback_last_kp_output(db, s.id)
    db.expire_all()
    assert db.get(Character, pc.id).system_data["firstAidUsed"] is True


def test_无source的san_check_下一轮照常结算(db):
    s, pc, mate, _module = _seed(db)
    kv = {"chars": "阿尔法", "success_loss": "3", "failure_loss": "3"}
    session_service.add_event(db, s.id, "action", "第一轮", actor_id=pc.id, actor_name=pc.name)
    asyncio.run(turn_effects._exec_san_check(db, s.id, s, kv, pc, [mate]))
    session_service.add_event(db, s.id, "action", "第二轮", actor_id=pc.id, actor_name=pc.name)
    asyncio.run(turn_effects._exec_san_check(db, s.id, s, kv, pc, [mate]))
    assert _san(db, mate.id) == 44


def test_真人无source的san待投_重复下发不再挂第二张(db):
    s, pc, _mate, _module = _seed(db)
    sd = dict(pc.system_data); sd["sanity"] = {"current": 50, "max": 99}; pc.system_data = sd; db.commit()
    session_service.add_event(db, s.id, "action", "我掀开床单", actor_id=pc.id, actor_name=pc.name)
    kv = {"chars": "亨利", "success_loss": "1", "failure_loss": "1d4"}
    _c, _d, pending = asyncio.run(turn_effects._exec_san_check(db, s.id, s, kv, pc, []))
    assert pending
    asyncio.run(turn_effects._exec_san_check(db, s.id, s, kv, pc, []))
    db.expire_all()
    checks = (db.get(GameSession, s.id).turn_state or {})["pending_checks"]
    assert len(checks) == 1
    assert next(iter(checks.values()))["san_key"].startswith("turn:")


@pytest.mark.parametrize("failure_at", ["event", "sync", "commit"])
def test_HP效果中途失败不会留下角色修改或孤立事件(db, monkeypatch, failure_at):
    s, pc, _mate, module = _seed(db)
    cid, sid = pc.id, s.id
    original_add = session_service.add_event
    original_commit = db.commit

    def fail_event(*args, **kwargs):
        original_add(*args, **kwargs)
        raise RuntimeError("事件写入后失败")

    def fail(*args, **kwargs):
        raise RuntimeError("提交中断探针")

    if failure_at == "event":
        monkeypatch.setattr(session_service, "add_event", fail_event)
    elif failure_at == "sync":
        monkeypatch.setattr(turn_effects, "_sync_combatant_hp", fail)
    else:
        monkeypatch.setattr(db, "commit", fail)
    with pytest.raises(RuntimeError):
        asyncio.run(turn_effects._exec_hp_change(
            db, sid, pc, "player", "-4", "失败探针", module=module,
        ))
    monkeypatch.setattr(db, "commit", original_commit)
    assert _hp(db, cid) == 15
    assert session_service.get_session_events(db, sid) == []
    # 回滚后 Session 仍可使用，重试只产生一次效果。
    monkeypatch.undo()
    asyncio.run(turn_effects._exec_hp_change(db, sid, pc, "player", "-4", "重试", module=module))
    assert _hp(db, cid) == 11


def test_HP工具文本与计划同轮提案去重且重生成后可重做(db):
    s, pc, mate, module = _seed(db)
    session_service.add_event(db, s.id, "action", "穿过火场", actor_id=pc.id)
    for source in ("kp_tool", "kp_text", "planner"):
        asyncio.run(turn_effects._exec_hp_change(
            db, s.id, pc, "player", "-3", "火焰烧伤", module=module,
            teammates=[mate], source=source,
        ))
    assert _hp(db, pc.id) == 12
    session_service.add_event(db, s.id, "narration", "火焰灼伤皮肤。", actor_name="KP")
    session_service.rollback_last_kp_output(db, s.id)
    assert _hp(db, pc.id) == 15
    asyncio.run(turn_effects._exec_hp_change(
        db, s.id, pc, "player", "-3", "火焰烧伤", module=module, source="kp_tool",
    ))
    assert _hp(db, pc.id) == 12
    session_service.add_event(db, s.id, "action", "再次穿过火场", actor_id=pc.id)
    asyncio.run(turn_effects._exec_hp_change(
        db, s.id, pc, "player", "-3", "火焰烧伤", module=module, source="kp_tool",
    ))
    assert _hp(db, pc.id) == 9


def test_真人KP连续扣血不被自动提案去重(db):
    s, pc, _mate, module = _seed(db)
    for _ in range(2):
        asyncio.run(turn_effects._exec_hp_change(db, s.id, pc, "player", "-2", "受伤", module=module))
    assert _hp(db, pc.id) == 11


def test_后台快照在新事件或世界变更后失效(db):
    from app.services.housekeeping_snapshot import Snapshot
    s, pc, _mate, _module = _seed(db)
    snapshot = Snapshot.capture(db, s)
    assert snapshot.is_current(db, s)
    session_service.add_event(db, s.id, "action", "下一轮", actor_id=pc.id)
    assert not snapshot.is_current(db, s)
    snapshot = Snapshot.capture(db, s)
    s.world_state = {"new_fact": "必须保留"}
    db.commit()
    assert not snapshot.is_current(db, s)
    assert s.world_state["new_fact"] == "必须保留"


@pytest.mark.parametrize("failure_at", ["rule", "dice_event", "conditions"])
def test_重伤分支失败时整次伤害回滚(db, monkeypatch, failure_at):
    from app.services import character_service, rule_options_service
    s, pc, _mate, module = _seed(db)
    cid, sid = pc.id, s.id
    real_rs = rule_options_service.ruleset_by_id
    real_add = session_service.add_event

    class _BrokenCheck:
        def resolve_skill_check(self, *a, **k):
            raise RuntimeError("体质检定规则异常")

    class _Proxy:
        def __init__(self, inner):
            self._inner = inner
            self.check = _BrokenCheck()

        def __getattr__(self, name):
            return getattr(self._inner, name)

    def fail_dice(db_, sid_, kind, *a, **k):
        if kind == "dice":
            raise RuntimeError("检定事件写入失败")
        return real_add(db_, sid_, kind, *a, **k)

    def fail_conditions(*a, **k):
        raise RuntimeError("状态写入失败")

    if failure_at == "rule":
        monkeypatch.setattr(rule_options_service, "ruleset_by_id", lambda d, i: _Proxy(real_rs(d, i)))
    elif failure_at == "dice_event":
        monkeypatch.setattr(session_service, "add_event", fail_dice)
    else:
        monkeypatch.setattr(character_service, "change_conditions", fail_conditions)
    with pytest.raises(RuntimeError):
        # 8 ≥ 15/2 → 重伤且未归零，走体质检定分支
        asyncio.run(turn_effects._exec_hp_change(db, sid, pc, "player", "-8", "斧劈", module=module))
    db.expire_all()
    char = db.get(Character, cid)
    assert char.system_data["hitPoints"]["current"] == 15
    assert char.status != "major_wound"
    assert not (char.system_data or {}).get("conditions")
    assert session_service.get_session_events(db, sid) == []


def test_同轮不同理由或数值的伤害不被误合并(db):
    s, pc, mate, module = _seed(db)
    session_service.add_event(db, s.id, "action", "冲进走廊", actor_id=pc.id)
    for delta, reason in (("-2", "被咬"), ("-2", "被抓"), ("-3", "被咬")):
        asyncio.run(turn_effects._exec_hp_change(
            db, s.id, pc, "player", delta, reason, module=module, source="kp_tool",
        ))
    assert _hp(db, pc.id) == 8
    # 同数值同理由但不同角色，也是两次独立效果
    asyncio.run(turn_effects._exec_hp_change(
        db, s.id, pc, "阿尔法", "-2", "被咬", module=module, teammates=[mate], source="kp_tool",
    ))
    db.expire_all()
    assert db.get(Character, mate.id).system_data["hitPoints"]["current"] == 10


@pytest.mark.parametrize("failure_at", ["event", "madness"])
def test_SAN结算中途失败_数值事件台账一并回滚(db, monkeypatch, failure_at):
    s, _pc, mate, _module = _seed(db)
    sid, mid = s.id, mate.id

    def boom(*a, **k):
        raise RuntimeError("SAN 结算中断")

    if failure_at == "event":
        monkeypatch.setattr(session_service, "add_event", boom)
    else:
        monkeypatch.setattr(turn_effects, "_apply_madness_status", boom)
    with pytest.raises(RuntimeError):
        turn_effects._settle_san_target(db, sid, s, mate, "1", "1d6", "地窖里的尸体")
    monkeypatch.undo()
    assert _san(db, mid) == 50
    assert session_service.get_session_events(db, sid) == []
    led = db.get(GameSession, sid).ledger
    assert not (led and led.san_checked)
    # 回滚后重试：只结算一次，并记下恐怖源台账
    chunks, desc = turn_effects._settle_san_target(
        db, sid, db.get(GameSession, sid), db.get(Character, mid), "1", "1", "地窖里的尸体",
    )
    assert _san(db, mid) == 49 and "理智检定" in desc
    db.expire_all()
    assert "地窖里的尸体|" + mid in db.get(GameSession, sid).ledger.san_checked
    dice = [e for e in session_service.get_session_events(db, sid) if e.event_type == "dice"]
    assert dice[0].metadata_["effect"]["kind"] == "san_check"
    assert "effect" not in (chunks[0].metadata or {})   # 凭据不随广播下发


def test_SAN待投卡消费与结算同事务_重复点击只扣一次_失败卡仍在(db, monkeypatch):
    s, _pc, mate, _module = _seed(db)
    sid, mid = s.id, mate.id
    session_service.add_pending_check(db, sid, {"id": "c1", "kind": "san_check", "char_id": mid})

    def boom(*a, **k):
        raise RuntimeError("中断")

    monkeypatch.setattr(session_service, "add_event", boom)
    with pytest.raises(RuntimeError):
        turn_effects._settle_san_target(db, sid, s, mate, "1", "1", "低语", consume_check_id="c1")
    monkeypatch.undo()
    assert _san(db, mid) == 50
    assert session_service.get_pending_check(db, sid, "c1") is not None   # 失败：卡还在，可重投

    for _ in range(2):   # 重复点击
        turn_effects._settle_san_target(
            db, sid, db.get(GameSession, sid), db.get(Character, mid), "1", "1", "低语",
            consume_check_id="c1",
        )
    assert _san(db, mid) == 49
    assert session_service.get_pending_check(db, sid, "c1") is None
