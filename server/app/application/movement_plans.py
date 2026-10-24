"""把 AI 规划格式转换为移动领域输入，保留旧 scene_change 的兼容语义。"""
from app.ai.turn_planner import TurnPlan
from app.domain.movement import MovementIntent


def movement_proposals(plan: TurnPlan | None) -> tuple[list[MovementIntent], bool]:
    if plan is None:
        return [], False
    moves = [
        MovementIntent(
            actors=tuple(m.actors), stance=m.stance, exit=m.exit, target=m.target,
            solo=m.solo, blocked=m.blocked, check_skill=m.check_skill,
            shared_check=m.shared_check,
        )
        for m in plan.scene_policy.moves if m.actors or m.target or m.exit
    ]
    if moves:
        return moves, False
    ref = (plan.scene_policy.scene_change or "").strip()
    return ([MovementIntent(target=ref)], True) if ref else ([], False)
