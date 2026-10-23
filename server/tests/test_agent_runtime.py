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


def test_必须保留内容超预算时显式失败():
    import pytest
    from app.services.agent_runtime import ContextBudgetExceeded
    with pytest.raises(ContextBudgetExceeded):
        ContextBundle("秘密约束" * 100, [{"role": "user", "content": "当前动作"}], 30).fit()


def test_中途系统约束保留且旧工具调用与结果一起裁剪():
    bundle = ContextBundle.from_messages([
        {"role": "system", "content": "身份"},
        {"role": "assistant", "content": "旧叙事" * 80, "tool_calls": [{"id": "a"}]},
        {"role": "tool", "tool_call_id": "a", "content": "结算完成"},
        {"role": "system", "content": "不可泄露"},
        {"role": "user", "content": "现在的动作"},
    ], budget_tokens=40).fit()
    assert [m["role"] for m in bundle.messages] == ["system", "user"]
    assert bundle.metadata["dropped_messages"] == 2
    assert bundle.metadata["estimated_tokens"] <= 40


def test_工具续写轮仍钉住最新玩家输入与完整工具交互():
    bundle = ContextBundle.from_messages([
        {"role": "system", "content": "身份"},
        {"role": "user", "content": "旧回合" * 80},
        {"role": "user", "content": "我推开地下室的门"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "t1"}, {"id": "t2"}]},
        {"role": "tool", "tool_call_id": "t1", "content": "侦查成功"},
        {"role": "tool", "tool_call_id": "t2", "content": "SAN 0/1"},
    ], budget_tokens=80).fit()
    assert [m.get("content") for m in bundle.messages][0] == "我推开地下室的门"
    assert [m.get("tool_call_id") for m in bundle.messages if m["role"] == "tool"] == ["t1", "t2"]
    assert bundle.for_model()[0] == {"role": "system", "content": "身份"}


def test_planner_上下文超预算时降级为无计划而非截断约束():
    import asyncio
    from app.ai import turn_planner

    class LLM:
        called = False

        async def complete(self, *a, **k):
            LLM.called = True
            return "{}"

    plan = asyncio.run(turn_planner.run_turn_planner(LLM(), [
        {"role": "system", "content": "规则" * 20000},
        {"role": "user", "content": "行动"},
    ]))
    assert plan is None and not LLM.called
