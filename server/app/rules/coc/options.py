"""CoC 村规参数：把引擎里写死的裁定阈值抽成一层可覆盖的配置。

**读时覆盖，绝不写回**。引擎每次结算现读这份配置，不把它烙进角色卡、事件或任何
已落库的数据——关掉村规就立刻回到 RAW，不留残迹。这一条抄自 Foundry 的 Active
Effects：它把改动应用在角色数据的副本上而非原件上，所以效果一撤销，原值自然回来。
反过来做（开村规时把新阈值写进存档）迟早会遇到「关不掉的村规」。

**缺省即 RAW**：所有字段的默认值 = 现行硬编码行为，逐字相同。存量存档的
``rule_options`` 是空 dict，走 :data:`DEFAULT_OPTIONS`，行为与本特性上线前完全一致，
不需要数据迁移。

取值一律经 :func:`from_dict` 白名单化并钳到合法区间：村规配置是房主从界面填进来的
外部输入，非法值不该有机会流到掷骰逻辑里去（"大成功阈值 = 100" 会让每一骰都是大成功）。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace

#: 大失败判定规则。
#: - ``raw``：CoC 7e 原文——掷出 100 必大失败，技能值 < 50 时 96 起即大失败；
#: - ``hundred_only``：只有 100 才是大失败（最常见的一条村规，嫌新手角色太容易翻车）；
#: - ``ninety_six_plus``：96 起一律大失败，不看技能值高低。
FUMBLE_RULES = ("raw", "hundred_only", "ninety_six_plus")

#: 临时疯狂的触发口径。
#: - ``fifth_of_san``：单次损失 ≥ 当前 SAN 的五分之一（本项目一直以来的口径）；
#: - ``flat``：单次损失 ≥ :attr:`CocRuleOptions.insanity_flat_threshold` 点（CoC 7e 原文是 5）。
INSANITY_RULES = ("fifth_of_san", "flat")


@dataclass(frozen=True)
class CocRuleOptions:
    """一局（或一个模组）生效的村规。字段默认值 = RAW，改一项只影响那一项。"""

    # ── 判定 ──────────────────────────────────────────────────────────
    #: 骰值 ≤ 此值 = 大成功。RAW 只有 01；放宽到 5 是常见村规。
    critical_max: int = 1
    fumble_rule: str = "raw"
    #: 单次检定最多叠几个奖励/惩罚骰。
    dice_pool_cap: int = 2

    # ── 幸运消费（CoC 7e 官方可选规则，Keeper Rulebook p.99；默认关）────
    #: 开启后，失败的检定可花 1 点幸运抵 1% 差值买成功。
    luck_spend: bool = False
    #: 单次检定最多花几点幸运（0 = 不限，照原文）。
    luck_spend_max: int = 0
    #: 战斗轮内是否允许花幸运（原文允许；「战斗中禁用」是常见村规）。
    luck_spend_in_combat: bool = True
    #: 花幸运买来的成功是否不计成长勾（原文：不计——走运没教会你任何事）。
    luck_spend_blocks_improvement: bool = True

    # ── 伤害与状态 ────────────────────────────────────────────────────
    #: 单击伤害 ≥ 最大 HP ÷ 此值 → 重伤。RAW 是半血。
    major_wound_divisor: int = 2
    insanity_rule: str = "fifth_of_san"
    #: ``insanity_rule="flat"`` 时的固定阈值。
    insanity_flat_threshold: int = 5

    # ── 成长 ──────────────────────────────────────────────────────────
    #: 关掉则不做技能成长检定（一夜之间的短模组常这么跑）。
    improvement: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    def diff_from_default(self) -> dict:
        """只列出与 RAW 不同的项——落库与展示都用它，省得存一堆默认值。"""
        default = DEFAULT_OPTIONS
        return {
            f.name: getattr(self, f.name)
            for f in fields(self)
            if getattr(self, f.name) != getattr(default, f.name)
        }


DEFAULT_OPTIONS = CocRuleOptions()


def describe(options: CocRuleOptions) -> list[str]:
    """把**改动过的**那几项译成人话，供注入 KP 与规划器。

    只列差异：没改的项照规则原文，模型本来就知道，写出来白费 token。

    为什么要告诉 KP——机制由引擎执行，本不需要它知道；但它要**说话**。大失败阈值改了
    之后，玩家问「我掷 97 怎么不算大失败」，KP 会照原文答「技能低于 50 就是大失败」，
    跟屏幕上的判定对不上；幸运消费开着而它不知道，就会写出「已成定局」，紧接着系统
    弹出「花幸运扭转」。
    """
    lines: list[str] = []
    if options.critical_max != DEFAULT_OPTIONS.critical_max:
        lines.append(f"大成功：掷出 {options.critical_max} 及以下都算大成功（原文只有 01）")
    if options.fumble_rule == "hundred_only":
        lines.append("大失败：只有掷出 100 才算（技能低于 50 时的 96-99 不再是大失败）")
    elif options.fumble_rule == "ninety_six_plus":
        lines.append("大失败：96 及以上一律算，不看技能高低")
    if options.dice_pool_cap != DEFAULT_OPTIONS.dice_pool_cap:
        lines.append(
            f"奖励骰/惩罚骰单次最多 {options.dice_pool_cap} 个"
            + ("（即本局不使用奖惩骰）" if options.dice_pool_cap == 0 else "")
        )
    if options.luck_spend:
        detail = "玩家检定失败后可花幸运点抵掉差值买下成功"
        if options.luck_spend_max:
            detail += f"，单次最多 {options.luck_spend_max} 点"
        if not options.luck_spend_in_combat:
            detail += "，战斗中不可用"
        lines.append(
            f"幸运消费：已开启——{detail}。"
            "所以别把一次失败写成『已成定局、再无转圜』，玩家可能正要花幸运翻盘"
        )
    if options.major_wound_divisor != DEFAULT_OPTIONS.major_wound_divisor:
        lines.append(
            f"重伤线：单次伤害达到最大 HP 的 1/{options.major_wound_divisor} 即为重伤"
            "（原文是半血），描述伤势轻重时按这个来"
        )
    if options.insanity_rule == "flat":
        lines.append(
            f"临时疯狂：单次理智损失达到 {options.insanity_flat_threshold} 点即触发"
            "（原文是当前 SAN 的五分之一）"
        )
    if not options.improvement:
        lines.append("本局不做技能成长检定，收场时不要提『这次经历让你的技能有所长进』")
    return lines

#: 数值字段的合法区间（闭区间），越界一律钳进来而不是报错——房主填错一个数字，
#: 不该让整局掷不了骰。
_INT_BOUNDS = {
    "critical_max": (1, 20),
    "dice_pool_cap": (0, 3),
    "luck_spend_max": (0, 999),
    "major_wound_divisor": (1, 10),
    "insanity_flat_threshold": (1, 99),
}
_ENUM_CHOICES = {
    "fumble_rule": FUMBLE_RULES,
    "insanity_rule": INSANITY_RULES,
}


def from_dict(raw: dict | None) -> CocRuleOptions:
    """把外部 dict 规范化成 :class:`CocRuleOptions`：认识的键才要，值一律钳进合法区间。

    未知键直接丢弃（旧前端提交的、或以后删掉的字段不会把这里搞崩）；类型不对的值退回默认。
    """
    if not raw:
        return DEFAULT_OPTIONS
    known = {f.name: f.type for f in fields(CocRuleOptions)}
    patch: dict = {}
    for key, value in (raw or {}).items():
        if key not in known:
            continue
        if key in _ENUM_CHOICES:
            text = str(value or "").strip()
            if text in _ENUM_CHOICES[key]:
                patch[key] = text
            continue
        if key in _INT_BOUNDS:
            try:
                number = int(value)
            except (TypeError, ValueError):
                continue
            low, high = _INT_BOUNDS[key]
            patch[key] = max(low, min(high, number))
            continue
        patch[key] = bool(value)
    return replace(DEFAULT_OPTIONS, **patch) if patch else DEFAULT_OPTIONS


def merge(*layers: dict | None) -> dict:
    """按「后者覆盖前者」合并村规层（模组默认 → 会话）。

    与 ``style_presets`` 同一套层级约定：会话留空则继承模组、模组也留空则 RAW。
    这里不做 Foundry 那种 mode/priority——村规是标量参数，不存在「多来源叠加同一个值」，
    引进优先级只会让「我改的这一项到底生效没有」变得不可解释。
    """
    merged: dict = {}
    for layer in layers:
        for key, value in (layer or {}).items():
            if value is not None:
                merged[key] = value
    return merged
