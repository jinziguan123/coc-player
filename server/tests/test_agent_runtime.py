from app.services.agent_runtime import ContextBundle


def test_context_bundle_splits_system_and_round_trips():
    bundle = ContextBundle.from_messages(
        [
            {"role": "system", "content": "你是 NPC"},
            {"role": "user", "content": "玩家问"},
        ],
        budget_tokens=8000,
        visibility=("npc",),
    )
    assert bundle.system == "你是 NPC"
    assert bundle.messages == [{"role": "user", "content": "玩家问"}]
    assert bundle.budget_tokens == 8000
    assert bundle.visibility == ("npc",)
    assert bundle.for_model() == [
        {"role": "system", "content": "你是 NPC"},
        {"role": "user", "content": "玩家问"},
    ]


def test_context_bundle_without_system_is_passthrough():
    bundle = ContextBundle.from_messages(
        [{"role": "user", "content": "hi"}], budget_tokens=100,
    )
    assert bundle.system == ""
    assert bundle.for_model() == [{"role": "user", "content": "hi"}]


def test_context_bundle_fit_drops_oldest_but_keeps_last():
    bundle = ContextBundle.from_messages(
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "old" * 50},
            {"role": "assistant", "content": "mid" * 50},
            {"role": "user", "content": "new"},
        ],
        budget_tokens=30,
    ).fit()
    assert bundle.messages[-1]["content"] == "new"
    assert len(bundle.messages) == 1
