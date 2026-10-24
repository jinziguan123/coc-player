"""移动领域契约：不需要数据库和模型替身即可验证通行规则。"""
import pytest

from app.domain.movement import (
    CheckOutcome, MovementIntent, PassageDecision, assess_passage, rollup, settle_check,
)


def test_路径不通优先于通道阻碍和检定():
    intent = MovementIntent(blocked="石板挡路", check_skill="力量")
    assert assess_passage(intent, reachable=False, legacy_gate="攀爬") == \
        PassageDecision("unreachable")


def test_通道阻碍不被检定需求覆盖():
    intent = MovementIntent(blocked="  石板挡路  ", check_skill="力量")
    assert assess_passage(intent, reachable=True, legacy_gate="攀爬") == \
        PassageDecision("blocked", reason="石板挡路")


@pytest.mark.parametrize("skill,legacy,expected", [
    (" 力量 ", "攀爬", "力量"),
    ("  ", "攀爬", "攀爬"),
    ("", "", ""),
])
def test_显式技能优先_缺省才使用旧门控(skill, legacy, expected):
    result = assess_passage(MovementIntent(blocked="  ", check_skill=skill),
                            reachable=True, legacy_gate=legacy)
    expected_status = "pending_check" if expected else "moved"
    assert result == PassageDecision(expected_status, skill=expected)


def settle(states, actor, success, *, shared=False, eligible=("a", "b")):
    return settle_check(states, CheckOutcome(frozenset({actor}), success),
                        shared=shared, eligible_ids=frozenset(eligible))


@pytest.mark.parametrize("shared", [False, True])
def test_先失败后成功_是否共享决定先前失败者能否通行(shared):
    initial = {"a": "pending", "b": "pending"}
    first = settle(initial, "a", False, shared=shared)
    second = settle(dict(first.states), "b", True, shared=shared)
    assert initial == {"a": "pending", "b": "pending"}
    assert second.mover_ids == (("a", "b") if shared else ("b",))
    assert rollup(dict(second.states)) == ("moved" if shared else "partial")
    assert not settle(dict(second.states), "b", True, shared=shared).changed


def test_共享障碍不能把已经离开的参与者搬回来():
    result = settle({"a": "tried", "b": "pending"}, "b", True,
                    shared=True, eligible=("b",))
    assert result.mover_ids == ("b",)
    assert dict(result.states) == {"a": "failed", "b": "moved"}


def test_无关检定不消费提案_全员失败才结束共同障碍():
    states = {"a": "tried", "b": "pending"}
    assert not settle(states, "other", True, shared=True).changed
    result = settle(states, "b", False, shared=True)
    assert dict(result.states) == {"a": "failed", "b": "failed"}
    assert rollup(dict(result.states)) == "failed"


def test_规划输入转换保留共享规则且不共享可变角色名单():
    from app.ai.turn_planner import MovePlan, ScenePolicy, TurnPlan
    from app.application.movement_plans import movement_proposals

    plan = TurnPlan(scene_policy=ScenePolicy(moves=[MovePlan(
        actors=["甲"], target="room", check_skill="力量", shared_check=True,
    )]))
    intents, legacy = movement_proposals(plan)
    plan.scene_policy.moves[0].actors.append("乙")
    assert intents[0].actors == ("甲",)
    assert intents[0].shared_check and not legacy
    old, legacy = movement_proposals(TurnPlan(scene_policy=ScenePolicy(scene_change="room")))
    assert legacy and old[0].target == "room" and not old[0].shared_check
