"""建卡规则（coc7.creation）：按建卡记录校验新卡。"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import get_db
from app.main import app
from app.models import Base
from app.rules import ruleset_for

RS = ruleset_for("coc")
C = RS.creation

# 25 岁（教育增强 1 次），属性都是 5 的倍数、在掷骰范围内
BASE = {"STR": 50, "CON": 60, "SIZ": 60, "DEX": 50, "APP": 60, "INT": 70, "POW": 60, "EDU": 70}


def start_skills(attrs):
    return dict(RS.character.base_skills(attrs))


def record(**kw):
    rec = {
        "method": "roll", "base_attributes": dict(BASE), "age": 25, "age_deductions": {},
        "occupation": "酒保", "occupation_picks": ["取悦", "说服", "神秘学"],
    }
    rec.update(kw)
    return rec


def card(attrs=None, **raise_by):
    """起始值之上加点；关键字 = 技能名、值 = 加多少（信用评级直接给值）。"""
    attrs = attrs or dict(BASE)
    skills = start_skills(attrs)
    skills["信用评级"] = 10
    for k, v in raise_by.items():
        if k == "信用评级":
            skills[k] = v
        else:
            skills[k] = skills.get(k, C.skill_start(k, attrs)) + v
    return attrs, skills


def check(rec, attrs, skills, options=None):
    rs = ruleset_for("coc", options or {})
    return rs.creation.validate_build(rec, attrs, skills)


# 酒保：职业点 EDU*2+APP*2 = 260；兴趣点 INT*2 = 140


def test_valid_build_passes():
    attrs, skills = card(取悦=40, 说服=40, 神秘学=30, 会计=50, 聆听=40, 潜行=40, 信用评级=20)
    assert check(record(), attrs, skills) == []


def test_interest_overspent():
    attrs, skills = card(潜行=70, 攀爬=71)   # 非本职 141 > 兴趣 140
    assert any("超过兴趣点" in e for e in check(record(), attrs, skills))


def test_occupation_points_cannot_go_to_other_skills():
    # 本职 0，非本职 200：兴趣点只有 140，职业点不能补到非本职上
    attrs, skills = card(潜行=70, 攀爬=70, 游泳=60)
    assert any("超过兴趣点" in e for e in check(record(), attrs, skills))


def test_interest_points_can_top_up_occupation_skills():
    # 本职 260 + 信用 20 = 280 > 职业点 260，但兴趣点还剩 140，可补
    attrs, skills = card(取悦=60, 说服=60, 会计=60, 聆听=60, 神秘学=20, 信用评级=20)
    assert check(record(), attrs, skills) == []


def test_credit_rating_range_and_pool():
    attrs, skills = card(信用评级=30)            # 酒保 8–25
    assert any("信用评级 30 不在" in e for e in check(record(), attrs, skills))


def test_group_picks_must_match():
    attrs, skills = card()
    errs = check(record(occupation_picks=["取悦", "神秘学"]), attrs, skills)
    assert any("两项社交技能：应选 2 项，选了 1 项" in e for e in errs)


def test_skill_cap_from_house_rules():
    attrs, skills = card(潜行=40)                # 20+40 = 60 > 其他上限 50
    errs = check(record(), attrs, skills, {"skill_cap_other": 50})
    assert any("潜行 加到 60，超过建卡上限 50" in e for e in errs)
    assert check(record(), attrs, skills) == []  # 默认上限 90 不受影响


def test_native_language_start_is_free_and_not_capped():
    # 母语起始 = EDU 70，高于房规上限 50：不算违规，也不花点
    attrs, skills = card()
    assert skills["母语"] == 70
    assert check(record(), attrs, skills, {"skill_cap_other": 50, "skill_cap_occupation": 50}) == []
    # 再往上加就超了
    attrs, skills = card(母语=5)
    errs = check(record(), attrs, skills, {"skill_cap_other": 50, "skill_cap_occupation": 50})
    assert any("母语 加到 75" in e for e in errs)


def test_native_language_specialization_starts_at_edu():
    assert C.skill_start("母语(中文)", BASE) == 70


def test_skill_below_start_rejected():
    attrs, skills = card()
    skills["侦查"] = 10
    assert any("低于起始值" in e for e in check(record(), attrs, skills))


def test_mythos_locked():
    attrs, skills = card(克苏鲁神话=5)
    assert any("克苏鲁神话 建卡时不能加点" in e for e in check(record(), attrs, skills))


# ── 年龄 ──────────────────────────────────────────────────────────────


def test_age_deductions_player_assigned():
    attrs = dict(BASE, STR=45, APP=55)            # 45 岁：STR/CON/DEX 合计扣 5，外貌 −5
    _, skills = card(attrs)
    rec = record(age=45, age_deductions={"STR": 5})
    assert check(rec, attrs, skills) == []
    # 分配总额不对
    errs = check(record(age=45, age_deductions={"STR": 3}), attrs, skills)
    assert any("合计扣 5 点" in e for e in errs)
    # 不能从外貌、体型扣
    errs = check(record(age=45, age_deductions={"SIZ": 5}), dict(attrs, SIZ=55, STR=50), skills)
    assert any("不能从 SIZ 扣点" in e for e in errs)


def test_edu_may_rise_within_improvement_checks():
    attrs = dict(BASE, EDU=79)                    # 25 岁 1 次增强：70–80
    _, skills = card(attrs)
    assert check(record(), attrs, skills) == []
    attrs = dict(BASE, EDU=85)
    _, skills = card(attrs)
    assert any("教育 85 超出" in e for e in check(record(), attrs, skills))


def test_teen_edu_penalty_and_str_siz():
    attrs = dict(BASE, EDU=65, SIZ=55)            # 少年：教育 −5，力量/体型合计扣 5
    _, skills = card(attrs)
    assert check(record(age=17, age_deductions={"SIZ": 5}), attrs, skills) == []


# ── 属性生成方式 ──────────────────────────────────────────────────────


def test_point_buy_total():
    base = {"STR": 90, "CON": 90, "SIZ": 90, "DEX": 50, "APP": 50, "INT": 50, "POW": 50, "EDU": 50}  # 520
    attrs, skills = card(dict(base))
    errs = check(record(method="buy", base_attributes=base), attrs, skills)
    assert any("超出 460" in e for e in errs)


def test_roll_values_must_be_multiples_of_five():
    base = dict(BASE, STR=52)
    attrs, skills = card(dict(base))
    assert any("不是掷骰能得到的值" in e for e in check(record(base_attributes=base), attrs, skills))


def test_quick_fire():
    base = {"STR": 40, "CON": 50, "SIZ": 50, "DEX": 50, "APP": 60, "INT": 60, "POW": 70, "EDU": 80}
    attrs = dict(base)
    skills = start_skills(attrs)
    skills["信用评级"] = 15
    for k, v in {"会计": 70, "聆听": 60, "心理学": 60, "侦查": 50, "取悦": 50, "说服": 50, "格斗(斗殴)": 40}.items():
        skills[k] = v
    for k in ("潜行", "攀爬", "游泳", "跳跃"):
        skills[k] += 20
    rec = record(method="quick", base_attributes=base, age=25, quick_boosts=["潜行", "攀爬", "游泳", "跳跃"])
    assert check(rec, attrs, skills) == []
    # 第二个 70 不在定值里
    skills["聆听"] = 70
    assert any("只能从" in e for e in check(rec, attrs, skills))
    # 属性不是那八个值
    bad = dict(base, STR=45)
    assert any("快速车卡的属性" in e for e in check(record(method="quick", base_attributes=bad), bad, skills))


def test_unknown_occupation():
    attrs, skills = card()
    assert any("不在本局职业表里" in e for e in check(record(occupation="不存在"), attrs, skills))


# ── 基本范围（所有卡）──────────────────────────────────────────────────


def test_basic_validation_allows_age_extremes():
    ok, errors = RS.character.validate_character({"base_attributes": dict(BASE, EDU=99, STR=5), "skills": {}})
    assert ok, errors
    ok, errors = RS.character.validate_character({"base_attributes": dict(BASE), "skills": {"侦查": 120}})
    assert not ok and "侦查" in errors[0]


# ── API ──────────────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'c.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    def override():
        d = factory()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_db] = override
    yield TestClient(app)
    app.dependency_overrides.clear()


def _payload(skills, rec):
    return {"name": "测试", "rule_system": "coc", "age": rec["age"], "base_attributes": dict(BASE),
            "skills": skills, "system_data": {}, "creation": rec}


def test_api_rejects_invalid_build_and_stores_record(client):
    _, skills = card(潜行=70, 攀爬=71)
    r = client.post("/api/characters", json=_payload(skills, record()))
    assert r.status_code == 400 and "超过兴趣点" in r.json()["detail"]

    _, skills = card(取悦=40, 说服=40, 神秘学=30, 信用评级=20)
    r = client.post("/api/characters", json=_payload(skills, record()))
    assert r.status_code == 200, r.text
    assert r.json()["system_data"]["creation"]["occupation"] == "酒保"


def test_api_without_record_only_basic_checks(client):
    _, skills = card(潜行=79)   # 没有建卡记录（导入卡）：不查点数
    r = client.post("/api/characters", json={**_payload(skills, record()), "creation": None})
    assert r.status_code == 200, r.text


def test_api_update_keeps_basic_range(client):
    _, skills = card()
    cid = client.post("/api/characters", json={**_payload(skills, record()), "creation": None}).json()["id"]
    r = client.put(f"/api/characters/{cid}", json={"skills": {"侦查": 150}})
    assert r.status_code == 400
    r = client.put(f"/api/characters/{cid}", json={"skills": {**skills, "侦查": 95}})
    assert r.status_code == 200, r.text   # 团中成长可以超建卡上限


def test_api_occupations_expose_groups_and_creation_rules(client):
    occs = client.get("/api/rules/coc/occupations").json()
    bartender = next(o for o in occs if o["name"] == "酒保")
    assert bartender["groups"] == [{"label": "两项社交技能", "pick": 2, "options": ["取悦", "话术", "恐吓", "说服"]}]
    assert "取悦" not in bartender["skills"]
    rules = client.get("/api/rules/coc/creation-rules").json()
    assert rules["skill_cap_occupation"] == 90 and rules["quick_attrs"] == [40, 50, 50, 50, 60, 60, 70, 80]


# ── AI 建卡走同一套规则 ──────────────────────────────────────────────


@pytest.mark.parametrize("age", [17, 25, 45, 85])
@pytest.mark.parametrize("occ_name", ["酒保", "会计师", "文物学家(原作向)", "医生"])
def test_ai_rule_build_passes_validation(age, occ_name):
    from app.services import ai_character_service as ai

    rs = ruleset_for("coc", {"skill_cap_other": 50})
    occ = rs.occupation.get_occupation(occ_name) or rs.occupation.occupations()[0]
    weak = {"STR": 20, "CON": 25, "SIZ": 50, "DEX": 30, "APP": 50, "INT": 60, "POW": 50, "EDU": 60}
    for attrs in (dict(BASE), weak):
        wanted = {"侦查": 80, "潜行": 70, "取悦": 60, "神秘学": 55, "说服": 90}
        final_attrs, skills, cr, rec = ai._rule_build(rs, attrs, age, occ, wanted, 99)
        assert rec is not None
        assert rs.creation.validate_build(rec, final_attrs, skills) == []
        assert occ.credit_min <= cr <= occ.credit_max


def test_required_deduction_floors_at_one():
    weak = dict(BASE, STR=20, CON=25, DEX=30)
    assert C.required_deduction(85, weak) == 19 + 24 + 29
    assert C.required_deduction(45, weak) == 5
