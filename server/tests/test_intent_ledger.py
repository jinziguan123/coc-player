from app.services import intent_ledger


def test_ledger_blocks_open_when_locked():
    ws = intent_ledger.record({}, "地下室的门", "npc", "lock", 1)
    assert intent_ledger.state(ws, "地下室的门") == "locked"
    assert intent_ledger.allows(ws, "地下室的门", "open") is False
    assert intent_ledger.allows(ws, "地下室的门", "enter") is False
    assert intent_ledger.allows(ws, "地下室的门", "block") is True


def test_ledger_unlock_and_version_increment():
    ws = intent_ledger.record({}, "门", "a", "lock", 1)
    ws = intent_ledger.record(ws, "门", "b", "unlock", 2)
    assert intent_ledger.state(ws, "门") == "open"
    assert intent_ledger.version(ws, "门") == 2
    assert intent_ledger.allows(ws, "门", "open") is True


def test_ledger_unknown_target_allows():
    assert intent_ledger.allows({}, "未知", "open") is True


def test_target_alias_resolves_to_existing_locked_door():
    ws = intent_ledger.record({}, "地下室的门", "npc", "lock", 1)
    assert intent_ledger.resolve_target(ws, "门") == "地下室的门"
    assert intent_ledger.allows(ws, "门", "open") is False
