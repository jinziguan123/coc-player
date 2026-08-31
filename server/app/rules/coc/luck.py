"""幸运消费（CoC 7e 可选规则，Keeper Rulebook p.99）：花 1 点幸运把骰值降 1。

**这是「放水」的正规出口**。KP 暗中降难度一旦被玩家察觉，整个骰子系统的可信度就没了；
而幸运消费花的是玩家自己的资源、由玩家自己拍板，明码标价，掉的每一点都记在卡上。

原文划下的边界（都在下面的 :func:`rescue_offer` 里逐条落实）：

- 只能改**自己**这一骰，且只能改**技能与属性检定**——SAN 与幸运检定不可用；
- **大失败不可挽回**，大成功也无从改起；
- 推动骰（pushed roll）之后不可再花幸运；
- 买来的成功**不计成长勾**——走运没教会你任何事。

余下几项（单次上限、战斗中是否可用）原文没管，是常见村规，交给 ``CocRuleOptions``。
"""

from __future__ import annotations

from app.rules.base import CheckResult
from app.rules.coc.checks import achieved_tier, judge, normalize_skill_name
from app.rules.coc.options import DEFAULT_OPTIONS, CocRuleOptions

#: 不可用幸运挽回的检定。SAN 与幸运本身按原文排除；「幸运」还有个更实际的理由——
#: 花幸运去买幸运检定的成功是拿自己垫自己，规则上说不通。
FORBIDDEN_SKILLS = ("SAN", "理智", "幸运", "运气", "LUCK")


def available_luck(character_data: dict) -> int:
    """角色当前可动用的幸运值。

    两种存法都要认：手动建卡写 ``system_data.luck``，AI 生成与 Excel 导入写
    ``base_attributes.LUCK``（``resolve_skill_value`` 那里也是这么兜的）。
    """
    system_data = character_data.get("system_data") or {}
    if system_data.get("luck") is not None:
        return max(0, int(system_data.get("luck") or 0))
    attrs = character_data.get("base_attributes") or {}
    return max(0, int(attrs.get("LUCK") or 0))


def is_forbidden_skill(skill_name: str) -> bool:
    name = (skill_name or "").strip()
    normalized = normalize_skill_name(name)
    return any(
        token == name or token == normalized or token in name
        for token in FORBIDDEN_SKILLS
    )


def rescue_offer(
    result: CheckResult,
    difficulty: str,
    character_data: dict,
    options: CocRuleOptions | None = None,
    *,
    in_combat: bool = False,
    pushed: bool = False,
) -> dict | None:
    """这一骰能不能用幸运救回来？能则返回可选项，否则 None。

    返回 ``{cost, reroll_cost, available, target}``：

    - ``cost``：补差额要花的点数（官方用法），**刚好够翻盘**——把骰值降到本次要求难度的
      及格线上，一点不多花。0 表示这条路不通（没开、差得比幸运还多、或超了单次上限）。
    - ``reroll_cost``：燃运重骰要烧的点数（村规用法）。0 表示没开或烧不起。

    两者各自独立：差 40 点而只有 30 点幸运时补不起，但烧 10 点重掷仍烧得起。
    """
    opts = options or DEFAULT_OPTIONS
    # 两种用法共同的前提。「孤注一掷之后不能再花幸运」是原文明写的
    # （「当技能检定失败时，玩家可以选择孤注一掷或是花费幸运，孤注一掷的结果不能用幸运值改变」）。
    if pushed or (not opts.luck_spend and not opts.luck_reroll):
        return None
    if in_combat and not opts.luck_spend_in_combat:
        return None
    if is_forbidden_skill(result.skill_name):
        return None
    # 大失败是掷出来的定局，不是差几点的问题；已经达标的更不必救。
    # 原文：「大成功、大失败与枪械故障总是要应用，不能通过幸运值改变」。
    if result.outcome == "fumble" or result.meets_difficulty:
        return None

    available = available_luck(character_data)
    gap = result.roll - result.target

    # 补差额（官方，规则书 p.85）：花到刚好够翻盘，一点不多花。
    cost = 0
    if opts.luck_spend and gap > 0 and gap <= available:
        if not (opts.luck_spend_max and gap > opts.luck_spend_max):
            cost = gap

    # 燃运重骰（村规）：定价买一次机会，与补差额**各自独立**——差得太多买不起时，
    # 烧运重掷往往还烧得起，那正是它存在的意义。
    reroll_cost = 0
    if opts.luck_reroll and 0 < opts.luck_reroll_cost <= available:
        reroll_cost = opts.luck_reroll_cost

    if not cost and not reroll_cost:
        return None
    return {
        "cost": cost,
        "reroll_cost": reroll_cost,
        "available": available,
        "target": result.target,
    }


def apply_rescue(
    result: CheckResult,
    difficulty: str,
    points: int,
    options: CocRuleOptions | None = None,
) -> CheckResult:
    """花掉 ``points`` 点幸运后的检定结果：骰值降下去，再走同一套判据重判。

    原件不改（``CheckResult`` 逐字段复制出一份新的）——与村规「读时覆盖」同一个道理，
    掷出来的原始骰值是既成事实，得留着给玩家看「我本来掷了多少、花了几点买回来」。
    """
    opts = options or DEFAULT_OPTIONS
    new_roll = max(1, result.roll - max(0, points))
    outcome, desc = judge(new_roll, result.skill_value, difficulty, result.target, opts)
    return CheckResult(
        skill_name=result.skill_name,
        skill_value=result.skill_value,
        roll=new_roll,
        target=result.target,
        outcome=outcome,
        description=desc,
        tier=achieved_tier(new_roll, result.skill_value, opts),
        meets_difficulty=outcome in ("critical_success", "hard_success", "success"),
        tens=list(result.tens),
        tens_kept=result.tens_kept,
        units=result.units,
        bonus=result.bonus,
        penalty=result.penalty,
    )


def apply_reroll(
    character_data: dict,
    skill_name: str,
    difficulty: str,
    bonus: int = 0,
    penalty: int = 0,
    options: CocRuleOptions | None = None,
):
    """燃运重骰：整骰重来一次，走的还是同一套 ``resolve_skill_check``。

    **重掷结果照单全收**——包括掷出大失败。买的是一次机会，不是一次成功；
    要是重掷还能接着花幸运补差额，这条村规就成了「幸运够多就永不失败」。
    """
    from app.rules.coc.checks import resolve_skill_check

    return resolve_skill_check(
        character_data, skill_name, difficulty, bonus, penalty, options or DEFAULT_OPTIONS,
    )


def deduct(character_data: dict, points: int) -> dict:
    """从角色数据里扣掉幸运，返回 ``{path, old, new}`` 供调用方落库。

    ``path`` 指明该写回哪一处（两种存法见 :func:`available_luck`），调用方据此更新角色卡。
    """
    points = max(0, points)
    system_data = character_data.get("system_data") or {}
    if system_data.get("luck") is not None:
        old = max(0, int(system_data.get("luck") or 0))
        return {"path": "system_data.luck", "old": old, "new": max(0, old - points)}
    attrs = character_data.get("base_attributes") or {}
    old = max(0, int(attrs.get("LUCK") or 0))
    return {"path": "base_attributes.LUCK", "old": old, "new": max(0, old - points)}
