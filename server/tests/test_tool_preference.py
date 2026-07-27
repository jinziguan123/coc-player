"""「有专用工具就用专用工具」这条原则的守卫。

背景：实测中 KP 发过 `[DICE_CHECK: skill=理智]` 而不是 `[SAN_CHECK]`，
一桌三个角色同时掷出「失败 (34 > 0)」——通用检定路径按普通技能查值，
而 SAN 不在技能表里，取到 0、目标值 0，必然全灭；而且不扣 SAN、不判疯狂。

引擎侧已补了取值回落（见 test_rules_coc 的理智用例），那是兜底；
真正的修法是让模型一开始就选对工具。提示词与工具描述是软约束、
最容易在后续编辑里被顺手删掉，所以在这里钉死。
"""

from app.ai.prompts.kp_system import KP_SYSTEM_PROMPT
from app.ai.tools import TOOLS_BY_NAME


def _tool(name: str):
    spec = TOOLS_BY_NAME.get(name)
    assert spec is not None, f"工具 {name} 不存在"
    return spec


class TestDiceCheckDefersToSpecialisedTools:
    def test_通用检定描述里点名让位给专用工具(self):
        desc = _tool("dice_check").description
        for expected in ("san_check", "opposed_check", "start_combat", "start_chase"):
            assert expected in desc, f"dice_check 描述未让位给 {expected}"

    def test_skill_参数明确禁止填理智(self):
        skill = _tool("dice_check").parameters["properties"]["skill"]["description"]
        assert "理智" in skill and "san_check" in skill, (
            "skill 参数说明应当明确「不要填理智，改用 san_check」"
        )

    def test_san_check_工具仍然存在且带损失参数(self):
        params = _tool("san_check").parameters["properties"]
        assert "success_loss" in params and "failure_loss" in params


class TestSystemPromptStatesThePrinciple:
    def test_提示词写明有专用指令就别拿通用检定凑(self):
        assert "有专用指令就用专用指令" in KP_SYSTEM_PROMPT

    def test_提示词点名禁止用通用检定做理智(self):
        assert "[DICE_CHECK: skill=理智]" in KP_SYSTEM_PROMPT
        assert "SAN_CHECK" in KP_SYSTEM_PROMPT

    def test_提示词说明了为什么不能凑(self):
        """只说『别这么做』容易被后来者当成可删的啰嗦；把后果写在旁边才留得住。"""
        assert "以 0 结算" in KP_SYSTEM_PROMPT

    def test_常驻段落不得宣传按需开放的检索指令(self):
        """RULE_LOOKUP / MODULE_LOOKUP 只在本局确实建了索引时才由上下文按需告知。
        写进常驻提示词会让 KP 发出无处可去的指令——补这条对照表时就踩过一次。"""
        assert "[RULE_LOOKUP" not in KP_SYSTEM_PROMPT
        assert "[MODULE_LOOKUP" not in KP_SYSTEM_PROMPT
