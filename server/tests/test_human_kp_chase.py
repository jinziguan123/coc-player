"""真人 KP 的追逐动作：发起 / 推进一轮 / 手动收场。

此前追逐只有 AI KP 能通过工具开启，真人 KP 的动作表里根本没有这一项。
这里验的是表单动作到 chase_service 状态机的接线，掷骰钉死、不调 AI。
"""

import asyncio

import pytest

from app.models import GameSession
from app.services import chase_service
from app.services.chat_service import execute_human_kp_action
from tests.test_human_kp import _db, _seed


def _seq(values):
    it = iter(values)
    return lambda: next(it)


def _act(db, session, module, action, payload):
    return asyncio.run(execute_human_kp_action(db, session.id, session, module, action, payload))


def test_start_chase_uses_module_npc_and_form_settings(tmp_path):
    db = _db(tmp_path)()
    module, hero, session = _seed(db)

    chunks, result = _act(db, session, module, "start_chase", {
        "quarry": hero.id, "pursuer": "守门人", "pursuer_mov": "9",
        "skill": "运动", "escape_at": "4", "caught_at": "-2", "start_gap": "1",
        "trigger": "守门人从门后扑了出来",
    })

    state = chase_service.get_chase(db.get(GameSession, session.id))
    assert state["quarry"]["name"] == "调查员"
    assert state["pursuer"]["name"] == "守门人" and state["pursuer"]["mov"] == 9
    assert (state["gap"], state["escape_at"], state["caught_at"]) == (1, 4, -2)
    assert "守门人" in result
    # 广播的追逐态带比拼技能与双方 MOV，但不带 NPC 的技能数值
    wire = next(c.as_wire() for c in chunks if c.as_wire().get("type") == "chase_start")
    meta = wire["metadata"]
    assert meta["skill"] == "运动" and meta["pursuer_mov"] == 9
    assert "skills" not in str(meta)


def test_start_chase_rejects_bad_setup(tmp_path):
    db = _db(tmp_path)()
    module, _hero, session = _seed(db)

    with pytest.raises(ValueError, match="指定追方"):
        _act(db, session, module, "start_chase", {"pursuer": ""})
    with pytest.raises(ValueError, match="在座的调查员"):
        _act(db, session, module, "start_chase", {"pursuer": "守门人", "quarry": "nobody"})
    with pytest.raises(ValueError, match="起始距离"):
        _act(db, session, module, "start_chase", {"pursuer": "守门人", "start_gap": "9"})

    _act(db, session, module, "start_chase", {"pursuer": "临场追兵"})
    with pytest.raises(ValueError, match="已经在追逐中"):
        _act(db, session, module, "start_chase", {"pursuer": "守门人"})


def test_start_chase_accepts_prefixed_refs_and_multiple_pursuers(tmp_path):
    db = _db(tmp_path)()
    module, hero, session = _seed(db)

    chunks, result = _act(db, session, module, "start_chase", {
        "quarries": f"character:{hero.id}",
        "pursuers": "guard,守门人,猎犬",
        "skill": "运动",
    })

    state = chase_service.get_chase(db.get(GameSession, session.id))
    assert [item["name"] for item in state["quarries"]] == ["调查员"]
    assert [item["name"] for item in state["pursuers"]] == ["守门人", "猎犬"]
    # 代表追兵从两名里按 MOV 取最快，而不是只认第一个
    assert state["pursuer"]["name"] in ("守门人", "猎犬")
    wire = next(c.as_wire() for c in chunks if c.as_wire().get("type") == "chase_start")
    assert wire["metadata"]["quarries"] == ["调查员"]
    assert wire["metadata"]["pursuers"] == ["守门人", "猎犬"]
    assert "调查员" in result and "猎犬" in result


def test_chase_round_moves_gap_and_reports_hazard(tmp_path, monkeypatch):
    db = _db(tmp_path)()
    module, _hero, session = _seed(db)
    _act(db, session, module, "start_chase", {"pursuer": "守门人"})

    # 逃方大成功、追方失败 → +2；障碍检定（逃方）失败 → 再 −1，净 +1
    monkeypatch.setattr("app.rules.coc.components.check.roll_percentile", _seq([1, 95, 99]))
    chunks, _ = _act(db, session, module, "chase_round", {
        "hazard": {"who": "quarry", "skill": "攀爬", "difficulty": "normal"},
    })

    state = chase_service.get_chase(db.get(GameSession, session.id))
    assert state["round"] == 1 and state["gap"] == 1
    dice = next(c.as_wire() for c in chunks if c.as_wire().get("type") == "dice")
    assert "障碍" in dice["content"] and "受阻" in dice["content"]
    # 实时那条也要带 actor：追逐台的逐轮记录按它筛
    assert dice.get("actor_name") == "追逐"


def test_end_chase_manual_and_outcomes(tmp_path):
    db = _db(tmp_path)()
    module, _hero, session = _seed(db)

    with pytest.raises(ValueError, match="不在追逐中"):
        _act(db, session, module, "end_chase", {"outcome": "manual"})

    _act(db, session, module, "start_chase", {"pursuer": "守门人"})
    chunks, _ = _act(db, session, module, "end_chase", {"outcome": "escaped"})

    session = db.get(GameSession, session.id)
    assert chase_service.get_chase(session) is None
    assert session.world_state["combat_result"]["outcome"] == "escaped"
    assert any(c.as_wire().get("type") == "chase_end" for c in chunks)
