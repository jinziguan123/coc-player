"""AI 建卡产出的角色卡要「填满」，而不是留几格给玩家自己补。

两处此前是空的：
- 居住地/出生地：提示词根本没要，角色卡上那一栏永远空着；
- 现金/消费水平/资产：只写了 creditRating，要玩家去编辑器里点一次「按信用评级换算」
  才补上——可那一步是纯查表，没有任何需要人来决定的东西。
"""

import pytest

from app.rules.coc.character import derive_assets, roll_attributes
from app.services import ai_character_service


class _FakeModule:
    """`_build_prompt` / `_default_occupation` 只读这几个字段。"""

    id = "mod-1"
    title = "测试模组"
    description = "一栋闹鬼的老宅"
    world_setting = {"era": "1920s"}


@pytest.fixture
def attrs():
    return roll_attributes()


def test_提示词索取居住地与出生地(attrs):
    prompt = ai_character_service._build_prompt(_FakeModule(), "", attrs)
    assert "residence" in prompt and "birthplace" in prompt
    assert "居住地" in prompt and "出生地" in prompt


def test_模型给的居住地出生地会落到角色卡上(attrs):
    ai = {
        "name": "林知微", "age": 30, "gender": "女",
        "residence": "波士顿", "birthplace": "内陆·安溪",
        "occupation": "记者", "credit_rating": 40, "skills": {"侦查": 60},
    }
    result = ai_character_service._assemble(_FakeModule(), attrs, ai)
    sd = result["system_data"]
    assert sd["residence"] == "波士顿"
    assert sd["birthplace"] == "内陆·安溪"


def test_模型没给就不写空字段(attrs):
    """留空好过写一个空串——角色卡按「有值才渲染」判断该不该出现这一栏。"""
    ai = {"name": "无名", "age": 25, "occupation": "记者", "credit_rating": 20, "skills": {}}
    sd = ai_character_service._assemble(_FakeModule(), attrs, ai)["system_data"]
    assert "residence" not in sd and "birthplace" not in sd


def test_资产按信用评级换算(attrs):
    ai = {"name": "有钱人", "age": 40, "occupation": "记者", "credit_rating": 60, "skills": {}}
    sd = ai_character_service._assemble(_FakeModule(), attrs, ai)["system_data"]

    # 信用评级会被钳到所选职业的合法区间，所以以落地后的那个值为准
    expected = derive_assets(sd["creditRating"])
    assert sd["cash"] == expected["cash"]
    assert sd["spendingLevel"] == expected["spendingLevel"]
    assert sd["assets"] == f"约 ${expected['assets']:,}"


def test_兜底卡也带资产(attrs):
    """LLM 挂掉时走的纯规则分支同样要填满——它产出的也是一张要用的卡。"""
    sd = ai_character_service._rule_only_fallback(_FakeModule(), attrs)["system_data"]
    expected = derive_assets(sd["creditRating"])
    assert sd["cash"] == expected["cash"]
    assert sd["spendingLevel"] == expected["spendingLevel"]
