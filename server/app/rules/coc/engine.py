"""CoC 7th Edition 规则引擎"""

from app.rules.base import CheckResult, RuleEngine
from app.rules.dice import roll, roll_percentile
from app.rules.coc.character import (
    COC_DEFAULT_SKILLS,
    apply_attr_derived_skills,
    build_default_skills,
    compute_derived,
    roll_attributes,
)
from app.rules.coc import options as coc_options
from app.rules.coc.checks import resolve_skill_check, san_check  # noqa: F401


class CoCRuleEngine(RuleEngine):
    def get_rule_system_id(self) -> str:
        return "coc"

    def get_character_schema(self) -> dict:
        return {
            "rule_system": "coc",
            "attributes": [
                {"key": "STR", "name": "力量", "roll": "3d6×5"},
                {"key": "CON", "name": "体质", "roll": "3d6×5"},
                {"key": "SIZ", "name": "体型", "roll": "(2d6+6)×5"},
                {"key": "DEX", "name": "敏捷", "roll": "3d6×5"},
                {"key": "APP", "name": "外貌", "roll": "3d6×5"},
                {"key": "INT", "name": "智力", "roll": "(2d6+6)×5"},
                {"key": "POW", "name": "意志", "roll": "3d6×5"},
                {"key": "EDU", "name": "教育", "roll": "(2d6+6)×5"},
            ],
            "derived": ["HP", "MP", "SAN", "MOV", "伤害加值", "体格", "幸运"],
            "system_specific_fields": ["sanity", "luck", "age", "occupation"],
            # 注意：母语/闪避在这里是占位的 0——它们由属性派生，静态表填不了真值。
            # 要拿「这组属性下的真实起始值」请走 base_skills()。
            "default_skills": COC_DEFAULT_SKILLS,
        }

    def base_skills(self, attrs: dict[str, int]) -> dict[str, int]:
        """CoC：静态表 + 母语(=EDU)、闪避(=DEX//2) 两项属性派生值。"""
        return build_default_skills(attrs)

    def create_character(self, data: dict) -> dict:
        attrs = data.get("base_attributes", {})
        if not attrs:
            attrs = roll_attributes()

        age = data.get("age", 25)
        system_data = compute_derived(attrs, age)

        extra_sd = data.get("system_data", {})
        if isinstance(extra_sd, dict):
            for k, v in extra_sd.items():
                if v is not None and v != "":
                    system_data[k] = v

        skills = data.get("skills")
        if not skills:
            skills = build_default_skills(attrs)
        else:
            # 客户端自带 skills（如前端加点）时，静态默认表缺少母语/闪避的属性
            # 派生值，这里兜底补齐，避免绕过 build_default_skills 导致两者为 0
            skills = apply_attr_derived_skills(dict(skills), attrs)

        return {
            "base_attributes": attrs,
            "skills": skills,
            "system_data": system_data,
        }

    def validate_character(self, character_data: dict) -> tuple[bool, list[str]]:
        errors = []
        attrs = character_data.get("base_attributes", {})
        required = ["STR", "CON", "SIZ", "DEX", "APP", "INT", "POW", "EDU"]

        for attr in required:
            val = attrs.get(attr)
            if val is None:
                errors.append(f"缺少属性: {attr}")
            elif not (15 <= val <= 90):
                errors.append(f"{attr} 值 {val} 超出合理范围 (15-90)")

        return (len(errors) == 0, errors)

    def resolve_check(
        self, character_data: dict, skill_name: str, difficulty: str = "normal",
        bonus: int = 0, penalty: int = 0, options: dict | None = None,
    ) -> CheckResult:
        return resolve_skill_check(
            character_data, skill_name, difficulty, bonus=bonus, penalty=penalty,
            options=coc_options.from_dict(options),
        )

    def improvement_check(self, current_value: int, options: dict | None = None) -> dict | None:
        """CoC 成长检定：d100 > 当前技能值（或 > 95）即成长，+1d10（上限 99）。

        家规关掉成长时返回 None（与「该规则系统不支持成长」同一个出口）。
        """
        if not coc_options.from_dict(options).improvement:
            return None
        r = roll_percentile()
        improved = r > current_value or r > 95
        gain = roll("1d10").total if improved else 0
        new_value = min(current_value + gain, 99) if improved else current_value
        return {
            "roll": r,
            "improved": improved and new_value > current_value,
            "gain": new_value - current_value,
            "old_value": current_value,
            "new_value": new_value,
        }

    # 伤害/重伤/濒死/死亡的权威结算见 combat.resolve_wound（战斗与叙事 HP_CHANGE 共用同一份规则）；
    # 此处不再另立一份简化 apply_damage，避免规则漂移。

    def roll_attribute_sets(self, count: int = 3) -> list[dict[str, int]]:
        """掷多组属性供玩家选择"""
        return [roll_attributes() for _ in range(count)]
