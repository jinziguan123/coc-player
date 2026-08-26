from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.rules.dice import roll


@dataclass
class CheckResult:
    skill_name: str
    skill_value: int
    roll: int
    target: int
    outcome: str  # critical_success | hard_success | success | failure | fumble（相对要求难度的成败，向后兼容）
    description: str
    # 「达成等级」：纯按骰值 vs 技能值算出的六档，与「要求难度」无关——信息量按它分层（req 2/3）。
    tier: str = "regular"  # critical | extreme | hard | regular | fail | fumble
    meets_difficulty: bool = True  # 是否达到本次「要求难度」的及格线（动作成败用）
    # d100 逐骰明细（供前端 3D 骰子动画严格还原）：无奖惩时 tens 只含 1 个、tens_kept==tens[0]。
    # roll 仍是最终 d100（= tens_kept + units，十位00+个位0 视作 100），既有字段/行为不变。
    tens: list[int] = field(default_factory=list)  # 所有掷出的十位（0/10/…/90）
    tens_kept: int = 0     # 最终采用的十位
    units: int = 0         # 个位骰（0-9）
    bonus: int = 0         # 奖励骰数量
    penalty: int = 0       # 惩罚骰数量


class RuleEngine(ABC):
    """规则引擎抽象基类"""

    @abstractmethod
    def get_rule_system_id(self) -> str: ...

    @abstractmethod
    def get_character_schema(self) -> dict:
        """返回角色卡字段定义 (JSON Schema 格式)"""
        ...

    def base_skills(self, attrs: dict[str, int]) -> dict[str, int]:
        """这组属性下、**未加任何点**时的技能起始值。

        与 `get_character_schema()["default_skills"]` 的区别只在于属性派生项：CoC 的
        母语=EDU、闪避=DEX//2 依赖具体角色，静态表里只能占位成 0。建卡界面必须拿这一份，
        否则会把占位的 0 当成真起始值展示——玩家为了把它填上去而花掉的点，落库时会被
        兜底的 `max(提交值, 派生值)` 顶掉，凭空蒸发。

        默认实现就是静态表：没有属性派生技能的规则系统无需覆写。
        """
        return dict(self.get_character_schema().get("default_skills") or {})

    @abstractmethod
    def create_character(self, data: dict) -> dict:
        """根据输入创建角色，自动计算派生属性"""
        ...

    @abstractmethod
    def validate_character(self, character_data: dict) -> tuple[bool, list[str]]: ...

    @abstractmethod
    def resolve_check(
        self, character_data: dict, skill_name: str, difficulty: str = "normal",
        bonus: int = 0, penalty: int = 0, options: dict | None = None,
    ) -> CheckResult:
        """``options`` 是本局家规（见各规则系统的 options 模块）；None = 该系统的 RAW。"""
        ...

    # 伤害/重伤/濒死/死亡结算见 app.rules.coc.combat.resolve_wound（不走 engine，避免规则漂移）。

    def roll_dice(self, notation: str):
        return roll(notation)

    def improvement_check(self, current_value: int, options: dict | None = None) -> dict | None:
        """技能成长检定（战后结算）。默认规则系统不支持成长，返回 None；
        支持的引擎（如 CoC）覆盖此方法，返回
        ``{"roll", "improved", "gain", "old_value", "new_value"}``。
        家规关掉成长时同样返回 None。"""
        return None
