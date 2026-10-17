from dataclasses import dataclass

from app.services.intent import TeamIntent, resolve_team_intents


@dataclass
class _Char:
    id: str
    name: str


A = _Char("a", "阿尔法")
B = _Char("b", "贝塔")
C = _Char("c", "伽马")


def _item(char, action="act", target="门", verb="", depends=(), seq=1):
    decision = {"action": action, "content": f"{char.name}行动", "target": target,
                "verb": verb, "depends_on": list(depends)}
    intent = TeamIntent(
        actor_id=char.id, actor_name=char.name, action=action,
        content=decision["content"], target=target, verb=verb,
        depends_on=tuple(depends), observes_seq=seq,
    )
    return char, decision, intent


def test_same_target_open_vs_block_conflicts():
    items = [_item(A, verb="open"), _item(B, verb="block")]
    resolved = resolve_team_intents(items)
    assert [r[2].actor_name for r in resolved] == ["阿尔法"]


def test_same_target_open_vs_enter_is_compatible():
    items = [_item(A, verb="open"), _item(B, verb="enter")]
    resolved = resolve_team_intents(items)
    assert {r[2].actor_name for r in resolved} == {"阿尔法", "贝塔"}


def test_depends_on_orders_prerequisite_first():
    items = [_item(B, verb="enter", depends=("阿尔法",)), _item(A, verb="open")]
    resolved = resolve_team_intents(items)
    assert [r[2].actor_name for r in resolved] == ["阿尔法", "贝塔"]


def test_missing_dependency_is_dropped():
    items = [_item(B, verb="enter", depends=("伽马",))]
    assert resolve_team_intents(items) == []


def test_dependency_on_committed_actor_is_allowed():
    items = [_item(B, verb="enter", depends=("阿尔法",))]
    resolved = resolve_team_intents(items, committed_actors={"a", "阿尔法"})
    assert [r[2].actor_name for r in resolved] == ["贝塔"]


def test_cycle_drops_branch():
    items = [
        _item(A, verb="open", depends=("贝塔",)),
        _item(B, verb="search", depends=("阿尔法",)),
    ]
    assert resolve_team_intents(items) == []
