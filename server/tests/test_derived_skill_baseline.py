"""属性派生技能（母语=EDU、闪避=DEX//2）的起始值与加点。

这两项的基础值取决于具体角色，静态的 COC_DEFAULT_SKILLS 里只能占位成 0。踩过的坑是
**界面照着那个 0 展示**：玩家以为这两项一片空白，往里加点填平，点池实实在在扣了点，
可落库时后端 `apply_attr_derived_skills` 的 `max(提交值, 派生值)` 会把它顶回派生值——
加到 EDU 以下的部分全部凭空蒸发，且退不回来。

修法是让建卡界面拿 `base_skills(attrs)` 而不是静态表，规则只在 RuleEngine 里算一次。
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.rules.coc.character import COC_DEFAULT_SKILLS
from app.rules.registry import get_engine

ATTRS = {"STR": 50, "CON": 50, "SIZ": 50, "DEX": 61, "APP": 50,
         "INT": 60, "POW": 50, "EDU": 70}


@pytest.fixture
def client():
    """纯规则计算端点，不碰数据库。"""
    return TestClient(app)


def test_静态表里这两项是占位的零():
    """钉住前提：不是「数据填漏了」，是它按定义就填不了。"""
    assert COC_DEFAULT_SKILLS["母语"] == 0
    assert COC_DEFAULT_SKILLS["闪避"] == 0


def test_base_skills_按属性填好派生值():
    skills = get_engine("coc").base_skills(ATTRS)
    assert skills["母语"] == 70          # = EDU
    assert skills["闪避"] == 30          # = DEX // 2，向下取整
    assert skills["侦查"] == COC_DEFAULT_SKILLS["侦查"]   # 其余照抄静态表


def test_base_skills_接口与引擎同源(client):
    r = client.post("/api/rules/coc/base-skills", json={"base_attributes": ATTRS})
    assert r.status_code == 200, r.text
    assert r.json()["skills"] == get_engine("coc").base_skills(ATTRS)


def test_按界面显示的起始值加点不会蒸发(client):
    """界面提交的是「起始值 + 加点」，落库后必须一分不少。

    回归：界面若拿静态表的 0，提交 0+20=20，落库被顶成 70，那 20 点白花。
    """
    base = get_engine("coc").base_skills(ATTRS)
    submitted = dict(base)
    submitted["母语"] = base["母语"] + 20      # 玩家加了 20 点
    submitted["闪避"] = base["闪避"] + 10

    landed = get_engine("coc").create_character(
        {"base_attributes": ATTRS, "age": 25, "skills": submitted},
    )["skills"]
    assert landed["母语"] == 90              # 70 + 20，一点没丢
    assert landed["闪避"] == 40              # 30 + 10


def test_教育高于九十时母语不被建卡上限砍掉():
    """90 是「加点不得超过」的上限，不是属性派生基础值的上限。EDU 上限是 99。"""
    attrs = {**ATTRS, "EDU": 95}
    assert get_engine("coc").base_skills(attrs)["母语"] == 95

    from app.services import ai_character_service

    class _Mod:
        id = "m"
        title = "测"
        description = ""
        world_setting = {"era": "1920s"}

    ai = {"name": "学者", "age": 40, "occupation": "记者", "credit_rating": 30, "skills": {}}
    assert ai_character_service._assemble(_Mod(), attrs, ai)["skills"]["母语"] == 95


def test_ai_建卡也允许给母语加点():
    """真正不可加点的只有克苏鲁神话。

    母语列在本仓多个职业的本职技能表里，本职技能表的含义就是「这些可以花本职点加」。
    从前 AI 路径把母语锁死并在最后硬覆写成 EDU，与手动建卡那条路（只锁克苏鲁神话）
    对不上：同一条规则，两条路两个结果。
    """
    from app.services import ai_character_service

    assert "母语" not in ai_character_service.NON_ALLOCATABLE
    assert "克苏鲁神话" in ai_character_service.NON_ALLOCATABLE

    class _Mod:
        id = "m"
        title = "测"
        description = ""
        world_setting = {"era": "1920s"}

    # 作家的本职技能表含母语；模型把它报到 85，应当真的加上去（而不是被顶回 EDU=70）
    ai = {"name": "作家", "age": 35, "occupation": "作家",
          "credit_rating": 20, "skills": {"母语": 85}}
    skills = ai_character_service._assemble(_Mod(), ATTRS, ai)["skills"]
    assert skills["母语"] > 70, "母语应当能在 EDU 之上加点"
