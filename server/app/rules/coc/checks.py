"""CoC 7th Edition 检定逻辑"""

import random
import re

from app.rules.base import CheckResult
from app.rules.coc.character import COC_DEFAULT_SKILLS
from app.rules.coc.options import DEFAULT_OPTIONS, CocRuleOptions
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

# 英文属性键 → 中文检定名。角色卡的 base_attributes 用英文键存，KP 偶尔照抄英文写成
# `skill=STR`——取值这一层碰巧认得（键本来就叫 STR），显示这一层却会原样打到玩家脸上，
# 检定卡上就出现「STR 检定」。取值宽容是好事，但宽容进来的写法得换回跑团用语再示人。
_CHARACTERISTIC_DISPLAY = {
    "STR": "力量", "CON": "体质", "SIZ": "体型", "DEX": "敏捷", "APP": "外貌",
    "INT": "智力", "POW": "意志", "EDU": "教育", "LUCK": "幸运",
    "SAN": "理智", "SANITY": "理智",
}


def display_skill_name(name: str) -> str:
    """检定名的对外写法：英文属性键换成中文，其余原样奉还。

    只做英文→中文这一个方向，刻意**不**顺手套 :func:`normalize_skill_name`：
    「灵感」按规则是 INT 直判，但它是个有名有姓的检定，显示成「智力」等于把 KP
    的意图抹平；「战斗」→「格斗(斗殴)」同理，那是取值时该做的归一，不是展示。
    """
    s = (name or "").strip()
    return _CHARACTERISTIC_DISPLAY.get(s.upper(), s)

# 技能名的书写变体：全角括号/冒号统一成半角括号形式（「射击：手枪」→「射击(手枪)」）。
# AI 解析模组时怎么写全看它心情，而技能查表是精确匹配——不归一就取不到值、按 0 结算。
_SKILL_PUNCT = str.maketrans({"（": "(", "）": ")", "：": ":", "﹕": ":", "　": "", " ": ""})
_SKILL_SPEC_RE = re.compile(r"^([^(:]+)[:(]([^)]*)\)?$")

# 同一项技能的俗称/简写 → 规范名。「战斗」是解析提示词早年示例带出来的写法，库里有一批。
_SKILL_ALIAS = {
    "战斗": "格斗(斗殴)", "斗殴": "格斗(斗殴)", "格斗": "格斗(斗殴)", "近战": "格斗(斗殴)",
    "射击": "射击(手枪)", "枪械": "射击(手枪)",
}


def _normalize_punct(name: str) -> str:
    """只做写法归一（不套别名）：「射击：手枪」「射击（手枪）」「射击 : 手枪」→「射击(手枪)」。"""
    s = (name or "").strip().translate(_SKILL_PUNCT)
    m = _SKILL_SPEC_RE.match(s)
    if m:
        base, spec = m.group(1), m.group(2)
        s = f"{base}({spec})" if spec else base
    return s


def normalize_skill_name(name: str) -> str:
    """技能名归一：写法归一后再套俗称别名。「战斗」→「格斗(斗殴)」。"""
    s = _normalize_punct(name)
    return _SKILL_ALIAS.get(s, s)


def _base_skill_value(skill_name: str, attrs: dict) -> int:
    """技能的规则基础值（角色卡没写这项时的底线）。

    CoC 里技能都有基础值：格斗(斗殴) 25、射击(手枪) 20、闪避 DEX/2……「没写」不等于 0。
    玩家角色卡是完整的所以照不出问题，模组 NPC 卡是稀疏的——不给底线，一个没写格斗的
    平民 NPC 挥拳就是必失败。属性派生的母语/闪避静态表里预置不了，这里按属性现算。
    """
    if skill_name == "闪避":
        return attrs.get("DEX", 0) // 2
    if skill_name == "母语":
        return attrs.get("EDU", 0)
    return COC_DEFAULT_SKILLS.get(skill_name, 0)


def resolve_skill_value(character_data: dict, skill_name: str) -> int:
    """取一次检定的技能值。查找顺序：精确 → 归一化 → 专精基础名 → 属性(含别名) → 幸运/理智 → 规则基础值。"""
    skills = character_data.get("skills") or {}
    attrs = character_data.get("base_attributes") or {}

    value = skills.get(skill_name) or attrs.get(skill_name, 0)
    if not value:
        # 归一化后再比一次：卡上写「射击：手枪」而检定要「射击(手枪)」时也能对上
        want = normalize_skill_name(skill_name)
        by_norm = {normalize_skill_name(k): v for k, v in skills.items()}
        value = by_norm.get(want) or 0
        if not value and "(" in want:
            # 卡上只写了不带专精的基础名（「射击: 60」）→ 拿来当该专精的值。只认卡上**本就没写
            # 专精**的条目：写了「射击(手枪) 70」的人拿步枪不该也按 70 算（RAW 各专精独立）。
            bare = {b: v for k, v in skills.items()
                    if "(" not in (b := _normalize_punct(k))}
            value = bare.get(want.split("(")[0]) or 0
    # 技能表/同名属性都没命中时，按属性骰别名回落到英文属性键（如 灵感→INT、智力→INT）
    if not value and skill_name in _CHARACTERISTIC_ALIAS:
        value = attrs.get(_CHARACTERISTIC_ALIAS[skill_name], 0)
    # 幸运骰的两种存法：手动建卡写 system_data.luck，AI 生成与 Excel 导入写
    # base_attributes.LUCK（上面的属性别名已覆盖后者）。这里补前者。
    # 漏掉任一处都会让「幸运」检定以 0 结算 —— 必失败，且提示写着「(34 > 0)」，
    # 玩家完全看不出是数据取错了位置。
    if not value and skill_name in ("幸运", "运气"):
        value = (character_data.get("system_data") or {}).get("luck") or 0
    # 理智骰同理：SAN 存于 system_data.sanity.current，既不在技能表也不在属性表。
    # KP 若发的是 [DICE_CHECK: skill=理智] 而非 [SAN_CHECK]，此前会以 0 结算 → 必失败
    # （实测一局里三名角色同时掷出「失败 (34 > 0)」就是这么来的）。
    # 注意：这里只修正**判定基数**；理智损失与疯狂发作仍只由 SAN_CHECK 路径处理，
    # 裸的理智检定不扣 SAN——这与手册一致（不是每次理智骰都伴随损失）。
    if not value and skill_name in _SANITY_ALIAS:
        sanity = (character_data.get("system_data") or {}).get("sanity") or {}
        value = sanity.get("current") or 0
    if not value:   # 都没有 → 该技能的规则基础值（而不是 0）
        value = _base_skill_value(normalize_skill_name(skill_name), attrs)
    return value

# 「达成等级」中文标签：纯按骰值算出的六档（与要求难度无关），用于检定提示与分层反馈。
TIER_LABEL_CN = {
    "critical": "大成功",
    "extreme": "极难成功",
    "hard": "困难成功",
    "regular": "普通成功",
    "fail": "普通失败",
    "fumble": "大失败",
}


def is_fumble(d100: int, skill_value: int, options: CocRuleOptions | None = None) -> bool:
    """这一骰算不算大失败——阈值由村规定（默认 RAW）。"""
    rule = (options or DEFAULT_OPTIONS).fumble_rule
    if rule == "hundred_only":
        return d100 == 100
    if rule == "ninety_six_plus":
        return d100 >= 96
    return d100 == 100 or (d100 >= 96 and skill_value < 50)


def achieved_tier(
    d100: int, skill_value: int, options: CocRuleOptions | None = None,
) -> str:
    """仅按骰值 vs 技能值判定达成的成功等级（与「要求难度」无关）。"""
    opts = options or DEFAULT_OPTIONS
    if d100 <= opts.critical_max:
        return "critical"
    if d100 <= skill_value // 5:
        return "extreme"
    if d100 <= skill_value // 2:
        return "hard"
    if d100 <= skill_value:
        return "regular"
    if is_fumble(d100, skill_value, opts):
        return "fumble"
    return "fail"


def judge(
    d100: int, skill_value: int, difficulty: str, target: int,
    options: CocRuleOptions | None = None,
) -> tuple[str, str]:
    """给定骰值判成败：返回 (outcome, 描述)。**只判不掷**。

    掷骰与判定分开，是为了让幸运消费能拿降低后的骰值重走同一套判据——买来的成功和
    掷出来的成功必须由同一段代码认定，否则迟早会出现「花了幸运却仍显示失败」这种账对不上的事。
    """
    opts = options or DEFAULT_OPTIONS
    if d100 <= opts.critical_max:
        return "critical_success", f"大成功！掷出了 {d100:02d}"
    if d100 <= skill_value // 5:
        return (
            "hard_success" if difficulty != "extreme" else "success",
            f"极难成功 ({d100} ≤ {skill_value // 5})",
        )
    if d100 <= skill_value // 2:
        if difficulty == "extreme":
            return "failure", f"失败 ({d100} > {skill_value // 5})"
        if difficulty == "hard":
            return "success", f"困难成功 ({d100} ≤ {skill_value // 2})"
        return "hard_success", f"困难成功 ({d100} ≤ {skill_value // 2})"
    if d100 <= target:
        return "success", f"成功 ({d100} ≤ {target})"
    if is_fumble(d100, skill_value, opts):
        return "fumble", f"大失败！掷出了 {d100}"
    return "failure", f"失败 ({d100} > {target})"


def resolve_skill_check(
    character_data: dict,
    skill_name: str,
    difficulty: str = "normal",
    bonus: int = 0,
    penalty: int = 0,
    options: CocRuleOptions | None = None,
) -> CheckResult:
    """CoC 技能检定

    难度等级:
    - normal: 普通（≤ 技能值）
    - hard: 困难（≤ 技能值/2）
    - extreme: 极难（≤ 技能值/5）

    奖励/惩罚骰（bonus/penalty，缺省 0，均为 0 时行为与旧版完全一致）：净奖惩>0 多掷十位
    取最有利、<0 取最不利，明细透传到 CheckResult 的 tens/tens_kept/units/bonus/penalty。

    ``options`` 是本局村规（大成功/大失败阈值、奖惩骰上限）；缺省即 RAW。
    """
    opts = options or DEFAULT_OPTIONS
    skill_value = resolve_skill_value(character_data, skill_name)
    # 奖惩骰上限也是村规：净奖惩先各自钳到上限内，再抵消。
    bonus = max(0, min(bonus, opts.dice_pool_cap))
    penalty = max(0, min(penalty, opts.dice_pool_cap))

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

    outcome, desc = judge(d100, skill_value, difficulty, target, opts)
    tier = achieved_tier(d100, skill_value, opts)
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


def san_check(
    character_data: dict,
    success_loss: str,
    failure_loss: str,
    options: CocRuleOptions | None = None,
) -> dict:
    """理智检定

    Args:
        success_loss: 成功时的 SAN 损失，如 "0" 或 "1d3"
        failure_loss: 失败时的 SAN 损失，如 "1d6" 或 "1d10"
        options: 本局村规（判定阈值、临时疯狂口径）；缺省即 RAW
    """
    from app.rules.dice import roll

    opts = options or DEFAULT_OPTIONS
    system_data = character_data.get("system_data", {})
    san = system_data.get("sanity", {})
    current_san = san.get("current", 0)

    check = resolve_skill_check(
        {"skills": {"SAN": current_san}, "base_attributes": {}},
        "SAN",
        options=opts,
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
        "went_insane": _went_insane(loss, current_san, opts),
    }


def _went_insane(loss: int, current_san: int, options: CocRuleOptions) -> bool:
    """这次损失够不够触发临时疯狂——口径由村规定。"""
    if options.insanity_rule == "flat":
        return loss >= options.insanity_flat_threshold
    return loss >= current_san // 5
