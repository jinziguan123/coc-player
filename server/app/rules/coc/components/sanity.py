"""coc7.sanity：理智检定与临时疯狂的触发口径。

槽位 ``rs.sanity``。村规参数见 :class:`SanityConfig`；未传村规时按其默认值（规则书原文）。
"""

from __future__ import annotations

from typing import Literal

from app.rules.coc.components.check import resolve_skill_check
from app.rules.config import RuleConfig, rule_field
from app.rules.dice import roll
from app.rules.kernel import Component, Provide

#: 临时疯狂的触发口径。
#: - ``fifth_of_san``：单次损失 ≥ 当前 SAN 的五分之一（本项目一直以来的口径）；
#: - ``flat``：单次损失 ≥ ``insanity_flat_threshold`` 点（CoC 7e 原文是 5）。
INSANITY_RULES = ("fifth_of_san", "flat")


class SanityConfig(RuleConfig):
    """coc7.sanity：临时疯狂的触发口径。"""

    insanity_rule: Literal["fifth_of_san", "flat"] = rule_field(
        "fifth_of_san", title="临时疯狂口径", choices={
            "fifth_of_san": ("SAN 的 1/5", "单次损失 ≥ 当前 SAN 的五分之一"),
            "flat": ("固定点数", "单次损失达到固定点数"),
        },
    )
    insanity_flat_threshold: int = rule_field(
        5, ge=1, le=99, title="固定点数", hint="规则书原文是 5",
        show_if={"insanity_rule": "flat"},
    )

    def describe(self) -> list[str]:
        if self.insanity_rule != "flat":
            return []
        return [f"临时疯狂：{insanity_trigger_text(self)}（本项目默认口径是当前 SAN 的五分之一）"]

    def labels(self) -> list[str]:
        return [f"临时疯狂 {self.insanity_flat_threshold} 点"] if self.insanity_rule == "flat" else []


_RAW = SanityConfig()


def insanity_trigger_text(options: SanityConfig) -> str:
    """临时疯狂触发口径的一句人话，与 ``checks.py`` 里 SAN 结算的判定同源。

    KP 手册（静态提示词）按 :data:`DEFAULT_OPTIONS` 生成这一句，本桌改过口径时
    :meth:`SanityConfig.describe` 再用同一函数给出改后的说法——两处不会各写一套而漂移。
    """
    if options.insanity_rule == "flat":
        return f"单次理智损失达到 {options.insanity_flat_threshold} 点即临时性疯狂"
    return "单次理智损失达到当前 SAN 的五分之一即临时性疯狂"


def san_check(
    character_data: dict,
    success_loss: str,
    failure_loss: str,
    options: SanityConfig | None = None,
) -> dict:
    """理智检定

    Args:
        success_loss: 成功时的 SAN 损失，如 "0" 或 "1d3"
        failure_loss: 失败时的 SAN 损失，如 "1d6" 或 "1d10"
        options: 本局村规（判定阈值、临时疯狂口径）；缺省即 RAW
    """
    opts = options or _RAW
    system_data = character_data.get("system_data", {})
    san = system_data.get("sanity", {})
    current_san = san.get("current", 0)

    check = resolve_skill_check(
        {"skills": {"SAN": current_san}, "base_attributes": {}},
        "SAN",
        options=options,
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


def _went_insane(loss: int, current_san: int, options: SanityConfig) -> bool:
    """这次损失够不够触发临时疯狂——口径由村规定。"""
    if options.insanity_rule == "flat":
        return loss >= options.insanity_flat_threshold
    return loss >= current_san // 5


COMPONENT = Component(
    id="coc7.sanity", title="理智",
    requires=("coc7.check",),
    config=SanityConfig, group="伤害与状态",
    provides={"sanity": Provide(
        __name__, bound=("san_check", "insanity_trigger_text"),
    )},
)
