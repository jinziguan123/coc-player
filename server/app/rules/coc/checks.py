"""CoC 7th Edition 检定逻辑"""

import random

from app.rules.base import CheckResult
from app.rules.dice import compose_d100, decompose_d100, roll_percentile


# 属性骰别名：base_attributes 用英文键存（INT/EDU…），但 KP/规则常用中文属性名或
# 「灵感(Idea)=智力」「知识(Know)=教育」这类基于属性的检定。统一映射到英文键。
_CHARACTERISTIC_ALIAS = {
    "力量": "STR", "体质": "CON", "体型": "SIZ", "敏捷": "DEX",
    "外貌": "APP", "智力": "INT", "意志": "POW", "教育": "EDU",
    "灵感": "INT", "知识": "EDU",  # CoC 7e：灵感=INT 直接判定，知识=EDU
    # 幸运也是一条属性别名。它落在哪儿取决于卡是怎么建的：手动建卡写
    # system_data.luck，AI 生成与 Excel 导入写 base_attributes.LUCK。
    # 两处都要能取到，否则「幸运」检定会以 0 结算、必失败。
    "幸运": "LUCK", "运气": "LUCK",
}

# 理智检定的写法：SAN 不在技能表也不在属性表，需单独回落到 system_data.sanity.current
_SANITY_ALIAS = ("理智", "san", "SAN", "Sanity", "sanity", "理智值")

# 「达成等级」中文标签：纯按骰值算出的六档（与要求难度无关），用于检定提示与分层反馈。
TIER_LABEL_CN = {
    "critical": "大成功",
    "extreme": "极难成功",
    "hard": "困难成功",
    "regular": "普通成功",
    "fail": "普通失败",
    "fumble": "大失败",
}


def achieved_tier(d100: int, skill_value: int) -> str:
    """仅按骰值 vs 技能值判定达成的成功等级（与「要求难度」无关）。"""
    if d100 == 1:
        return "critical"
    if d100 <= skill_value // 5:
        return "extreme"
    if d100 <= skill_value // 2:
        return "hard"
    if d100 <= skill_value:
        return "regular"
    if d100 == 100 or (d100 >= 96 and skill_value < 50):
        return "fumble"
    return "fail"


def resolve_skill_check(
    character_data: dict,
    skill_name: str,
    difficulty: str = "normal",
    bonus: int = 0,
    penalty: int = 0,
) -> CheckResult:
    """CoC 技能检定

    难度等级:
    - normal: 普通（≤ 技能值）
    - hard: 困难（≤ 技能值/2）
    - extreme: 极难（≤ 技能值/5）

    奖励/惩罚骰（bonus/penalty，缺省 0，均为 0 时行为与旧版完全一致）：净奖惩>0 多掷十位
    取最有利、<0 取最不利，明细透传到 CheckResult 的 tens/tens_kept/units/bonus/penalty。
    """
    skills = character_data.get("skills", {})
    attrs = character_data.get("base_attributes", {})

    skill_value = skills.get(skill_name) or attrs.get(skill_name, 0)
    # 技能表/同名属性都没命中时，按属性骰别名回落到英文属性键（如 灵感→INT、智力→INT）
    if not skill_value and skill_name in _CHARACTERISTIC_ALIAS:
        skill_value = attrs.get(_CHARACTERISTIC_ALIAS[skill_name], 0)
    # 幸运骰的两种存法：手动建卡写 system_data.luck，AI 生成与 Excel 导入写
    # base_attributes.LUCK（上面的属性别名已覆盖后者）。这里补前者。
    # 漏掉任一处都会让「幸运」检定以 0 结算 —— 必失败，且提示写着「(34 > 0)」，
    # 玩家完全看不出是数据取错了位置。
    if not skill_value and skill_name in ("幸运", "运气"):
        skill_value = (character_data.get("system_data") or {}).get("luck") or 0
    # 理智骰同理：SAN 存于 system_data.sanity.current，既不在技能表也不在属性表。
    # KP 若发的是 [DICE_CHECK: skill=理智] 而非 [SAN_CHECK]，此前会以 0 结算 → 必失败
    # （实测一局里三名角色同时掷出「失败 (34 > 0)」就是这么来的）。
    # 注意：这里只修正**判定基数**；理智损失与疯狂发作仍只由 SAN_CHECK 路径处理，
    # 裸的理智检定不扣 SAN——这与手册一致（不是每次理智骰都伴随损失）。
    if not skill_value and skill_name in _SANITY_ALIAS:
        sanity = (character_data.get("system_data") or {}).get("sanity") or {}
        skill_value = sanity.get("current") or 0

    if difficulty == "hard":
        target = skill_value // 2
    elif difficulty == "extreme":
        target = skill_value // 5
    else:
        target = skill_value

    # 基础 d100 走 roll_percentile 这个模块级接缝（测试会 monkeypatch 它钉死骰值）；
    # 由它拆出常规十位/个位。净奖惩 ≠ 0 时再额外掷十位并按取优/取劣重挑，个位不变。
    base_d100 = roll_percentile()
    base_tens, units = decompose_d100(base_d100)
    net = bonus - penalty
    tens = [base_tens] + [random.randint(0, 9) * 10 for _ in range(abs(net))]
    tens_kept = min(tens) if net > 0 else (max(tens) if net < 0 else base_tens)
    d100 = compose_d100(tens_kept, units)

    if d100 == 1:
        outcome = "critical_success"
        desc = "大成功！掷出了 01"
    elif d100 <= skill_value // 5:
        outcome = "hard_success" if difficulty != "extreme" else "success"
        desc = f"极难成功 ({d100} ≤ {skill_value // 5})"
    elif d100 <= skill_value // 2:
        if difficulty == "extreme":
            outcome = "failure"
            desc = f"失败 ({d100} > {skill_value // 5})"
        elif difficulty == "hard":
            outcome = "success"
            desc = f"困难成功 ({d100} ≤ {skill_value // 2})"
        else:
            outcome = "hard_success"
            desc = f"困难成功 ({d100} ≤ {skill_value // 2})"
    elif d100 <= target:
        outcome = "success"
        desc = f"成功 ({d100} ≤ {target})"
    elif d100 >= 96 and skill_value < 50:
        outcome = "fumble"
        desc = f"大失败！掷出了 {d100}"
    elif d100 == 100:
        outcome = "fumble"
        desc = "大失败！掷出了 100"
    else:
        outcome = "failure"
        desc = f"失败 ({d100} > {target})"

    tier = achieved_tier(d100, skill_value)
    meets = outcome in ("critical_success", "hard_success", "success")
    return CheckResult(
        skill_name=skill_name,
        skill_value=skill_value,
        roll=d100,
        target=target,
        outcome=outcome,
        description=desc,
        tier=tier,
        meets_difficulty=meets,
        tens=tens,
        tens_kept=tens_kept,
        units=units,
        bonus=bonus,
        penalty=penalty,
    )


def san_check(character_data: dict, success_loss: str, failure_loss: str) -> dict:
    """理智检定

    Args:
        success_loss: 成功时的 SAN 损失，如 "0" 或 "1d3"
        failure_loss: 失败时的 SAN 损失，如 "1d6" 或 "1d10"
    """
    from app.rules.dice import roll

    system_data = character_data.get("system_data", {})
    san = system_data.get("sanity", {})
    current_san = san.get("current", 0)

    check = resolve_skill_check(
        {"skills": {"SAN": current_san}, "base_attributes": {}},
        "SAN",
    )

    if check.outcome in ("critical_success", "hard_success", "success"):
        loss_expr = success_loss
    else:
        loss_expr = failure_loss

    # 纯数字 = 固定损失，不掷骰。CoC 的 SAN 损失本就常是「固定值/骰式」混排（0/1d3、
    # 1/1d6），planner 的指引也明确给出「血腥或怪物 1/1d6」——只特判 "0" 会让
    # "1"、"2" 落进 roll() 抛「无效的骰子表达式」，整次理智检定被吞。
    loss_expr = str(loss_expr or "0").strip()
    if loss_expr.isdigit():
        loss = int(loss_expr)
        loss_roll = None
    else:
        loss_roll = roll(loss_expr)
        loss = loss_roll.total

    new_san = max(0, current_san - loss)

    return {
        "check": check,
        "san_loss": loss,
        "loss_roll": loss_roll,   # DiceRollResult（损失骰池，供前端动画）；固定损失 "0" 时为 None
        "old_san": current_san,
        "new_san": new_san,
        "went_insane": loss >= current_san // 5,
    }
