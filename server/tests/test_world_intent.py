from app.services.world_intent import (
    HISTORY_KEY, from_combat, from_npc, list_history, record_state,
)


def test_npc_intent_records_in_history():
    intent = from_npc(
        actor_id="butler", actor_name="管家", target="门", verb="lock",
        content="我把门锁上。", observes_seq=7,
    )
    ws = record_state({}, intent)
    assert ws[HISTORY_KEY][-1]["source"] == "npc"
    assert ws[HISTORY_KEY][-1]["target"] == "门"
    assert ws[HISTORY_KEY][-1]["verb"] == "lock"


def test_combat_intent_uses_stable_npc_target():
    intent = from_combat(
        {"id": "npc_1", "name": "潜随者"},
        {"action": "attack", "target_id": "hero"},
        {"round": 3},
    )
    assert intent.source == "combat"
    assert intent.target == "char:hero"
    assert intent.verb == "attack"
    assert intent.observes_seq == 3


def test_history_is_trimmed():
    ws = {}
    for i in range(130):
        ws = record_state(ws, from_npc(
            actor_id="a", actor_name="A", verb="observe", content=str(i),
        ))
    assert len(ws[HISTORY_KEY]) == 100
    assert ws[HISTORY_KEY][-1]["content"] == "129"


def test_combat_precondition_reads_target_state():
    state = {
        "round": 1,
        "initiative": [
            {"id": "hero", "hp": 0, "status": "dead"},
            {"id": "ally", "hp": 8, "status": "ok"},
        ],
    }
    dead = from_combat({"id": "npc_1", "name": "潜随者"}, {"action": "attack", "target_id": "hero"}, state)
    alive = from_combat({"id": "npc_1", "name": "潜随者"}, {"action": "attack", "target_id": "ally"}, state)
    assert dead.precondition_ok is False
    assert alive.precondition_ok is True


def test_list_history_newest_first_and_limited():
    ws = {}
    for i in range(3):
        ws = record_state(ws, from_npc(actor_id="a", actor_name="A", verb="observe", content=str(i)))
    items = list_history(ws, limit=2)
    assert [i["content"] for i in items] == ["2", "1"]
