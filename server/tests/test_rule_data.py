"""P3：组件数据表——官方数据、补丁层（模组 → 村规）、严格校验、结算中生效、编辑器接口、房规文件。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.deps import require_local_client
from app.database import get_db
from app.main import app
from app.models import Base, Character, GameSession, Module  # noqa: F401
from app.rules import get_system, house_rules
from app.rules import data as rule_data
from app.services import rule_options_service

COC = get_system("coc")
WEAPONS = COC.data_table("weapons")
HAMMER = {"name": "雷霆之锤", "skill": "格斗(斗殴)", "dam": "2D8+DB", "category": "自定义"}


# ── 官方数据 ─────────────────────────────────────────────────────────────


def test_official_tables_load_from_yaml():
    counts = {t.name: len(t.official_rows) for t in COC.data_tables}
    assert counts == {
        "skills": 51, "occupations": 112, "specializations": 8,
        "equipment": 52, "weapons": 106, "madness_symptoms": 10,
    }


def test_official_occupation_formulas_all_match_the_safe_grammar():
    from app.rules.coc.components.occupation import _calc_points

    for occ in COC.data_table("occupations").official:
        assert _calc_points(occ.skill_formula, {"EDU": 50, "DEX": 60, "STR": 40}) > 0


# ── 补丁 ─────────────────────────────────────────────────────────────────


def test_patch_modifies_adds_and_deletes():
    rows = rule_data.apply_patches(WEAPONS, WEAPONS.official_rows, [
        {"name": "弓箭", "dam": "1D8+半DB"},
        HAMMER,
        {"name": "长鞭", "$delete": True},
    ])
    by = {r["name"]: r for r in rows}
    assert by["弓箭"]["dam"] == "1D8+半DB" and by["弓箭"]["range"] == "30"   # 只改了伤害
    assert by["雷霆之锤"]["round"] == "1"                                    # 新行补齐默认值
    assert "长鞭" not in by
    assert rows[-1]["name"] == "雷霆之锤"                                    # 新增行追加在后


def test_stored_bad_patch_row_is_skipped_not_fatal():
    rows = rule_data.apply_patches(WEAPONS, WEAPONS.official_rows, [
        {"name": "坏枪"},                 # 新行缺必填字段
        {"dam": "1D6"},                   # 缺主键
        {"name": "弓箭", "tho": 7},       # 越界
    ])
    assert len(rows) == 106 and {r["name"]: r for r in rows}["弓箭"]["tho"] == 0


def test_validate_patch_reports_each_problem():
    _, errors = rule_data.validate_patch(WEAPONS, WEAPONS.official_rows, [
        {"name": "坏枪"},
        {"dam": "1D6"},
        {"name": "弓箭", "tho": 7},
        {"name": "弓箭", "dam": "1D6"},
        {"name": "不存在的枪", "$delete": True},
        {"name": "斧", "mystery": 1, "skill": "x", "dam": "1"},
    ])
    text = "\n".join(errors)
    assert "缺少主键" in text and "出现了两次" in text and "要删除的行不存在" in text
    assert "不认识的字段：mystery" in text and "tho" in text and "skill" in text


def test_validate_patch_keeps_minimal_diff():
    norm, errors = rule_data.validate_patch(WEAPONS, WEAPONS.official_rows, [
        {"name": "弓箭", "dam": "1D6+半DB", "range": "40"},   # 伤害没变，只有射程变了
    ])
    assert errors == [] and norm == [{"name": "弓箭", "range": "40"}]


def test_diff_rows_from_full_table():
    desired = [dict(r) for r in WEAPONS.official_rows if r["name"] != "长鞭"] + [
        WEAPONS.row_model(**HAMMER).model_dump(),
    ]
    desired[1]["dam"] = "1D8"
    patch = rule_data.diff_rows(WEAPONS, WEAPONS.official_rows, desired)
    assert {"name": desired[1]["name"], "dam": "1D8"} in patch
    assert {"name": "长鞭", "$delete": True} in patch
    assert any(p["name"] == "雷霆之锤" and p["dam"] == "2D8+DB" for p in patch)
    assert len(patch) == 3


@pytest.mark.parametrize("formula", ["__import__('os')", "EDU*2+ZZZ*2", "EDU**2", "EDU*2-DEX*2", ""])
def test_occupation_formula_rejects_anything_but_the_grammar(formula):
    table = COC.data_table("occupations")
    _, errors = rule_data.validate_patch(table, table.official_rows, [
        {"name": "黑客", "skill_formula": formula, "skills": []},
    ])
    assert errors and "技能点公式" not in "" and any("skill_formula" in e for e in errors)


# ── 结算中生效 ───────────────────────────────────────────────────────────


def test_custom_weapon_resolves_in_combat_only_where_patched():
    patched = COC.ruleset({}, [{"weapons": [HAMMER]}])
    assert patched.combat.lookup_weapon("雷霆之锤")["dam"] == "2D8+DB"
    assert COC.ruleset().combat.lookup_weapon("雷霆之锤") is None
    assert patched.combat.weapon_categories()[-1] == "自定义"


def test_custom_skill_base_used_when_sheet_lacks_skill():
    rs = COC.ruleset({}, [{"skills": [{"name": "计算机使用", "base": 40}, {"name": "无线电", "base": 15}]}])
    assert rs.check.resolve_skill_value({"skills": {}}, "计算机使用") == 40
    assert rs.check.resolve_skill_value({"skills": {}}, "无线电") == 15
    assert rs.character.base_skills({"EDU": 60, "DEX": 50})["无线电"] == 15
    assert COC.ruleset().check.resolve_skill_value({"skills": {}}, "计算机使用") == 5


def test_custom_occupation_formula_drives_skill_points():
    rs = COC.ruleset({}, [{"occupations": [
        {"name": "黑客", "category": "数字游民", "skill_formula": "EDU*2+INT*2", "skills": ["计算机使用"]},
    ]}])
    assert rs.occupation.calc_occupation_points("黑客", {"EDU": 50, "INT": 70}) == 240
    assert rs.occupation.occupation_categories()[-1] == "数字游民"


def test_custom_symptom_table_drives_madness():
    only = {"key": "giggles", "label": "狂笑不止", "manifest": "止不住地笑", "penalty_skills": ["话术"]}
    rs = COC.ruleset({}, [{"madness_symptoms": [
        *({"key": r["key"], "$delete": True} for r in COC.data_table("madness_symptoms").official_rows),
        only,
    ]}])
    assert rs.madness.make_bout()["label"] == "狂笑不止"


def test_village_layer_overrides_module_layer():
    module_layer = {"weapons": [{**HAMMER, "dam": "1D10"}]}
    village_layer = {"weapons": [{"name": "雷霆之锤", "dam": "3D6"}]}
    rs = COC.ruleset({}, [module_layer, village_layer])
    assert rs.combat.lookup_weapon("雷霆之锤")["dam"] == "3D6"


# ── 服务层 ───────────────────────────────────────────────────────────────


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'd.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _session(db, module_data=None):
    m = Module(title="m", rule_system="coc", default_rule_data=module_data or {})
    db.add(m)
    db.commit()
    gs = GameSession(module_id=m.id, status="active", world_state={})
    db.add(gs)
    db.commit()
    return m, gs


def test_session_ruleset_stacks_module_then_village(db):
    _, gs = _session(db, {"weapons": [HAMMER]})
    assert rule_options_service.ruleset(db, gs).combat.lookup_weapon("雷霆之锤")
    rule_options_service.save_village_table(db, "coc", "weapons", [
        *(r for r in WEAPONS.official_rows), {**HAMMER, "dam": "9D9"},
    ])
    assert rule_options_service.ruleset(db, gs).combat.lookup_weapon("雷霆之锤")["dam"] == "9D9"


def test_village_switch_also_turns_off_data(db):
    _, gs = _session(db)
    rule_options_service.save_village_table(db, "coc", "weapons", [*WEAPONS.official_rows, HAMMER])
    assert rule_options_service.ruleset(db, gs).combat.lookup_weapon("雷霆之锤")
    rule_options_service.save_village_options(db, "coc", {}, enabled=False)
    assert rule_options_service.ruleset(db, gs).combat.lookup_weapon("雷霆之锤") is None


def test_saving_official_table_stores_nothing(db):
    patch = rule_options_service.save_village_table(db, "coc", "weapons", list(WEAPONS.official_rows))
    assert patch == [] and rule_options_service.village_row(db, "coc").data_patches == {}


def test_village_labels_summarize_data(db):
    rule_options_service.save_village_table(db, "coc", "weapons", [
        *(r for r in WEAPONS.official_rows if r["name"] != "长鞭"), HAMMER,
    ])
    assert "武器表：新增 1、删除 1" in rule_options_service.village_labels(db, "coc")


def test_save_with_invalid_data_changes_nothing(db):
    with pytest.raises(rule_options_service.RuleDataError):
        rule_options_service.save_village_options(
            db, "coc", {"critical_max": 5}, data={"weapons": [{"name": "坏枪"}]},
        )
    row = rule_options_service.village_row(db, "coc")
    assert row is None or row.options == {}


# ── 房规文件 v2 ──────────────────────────────────────────────────────────


def test_house_rules_roundtrip_with_data():
    text = house_rules.export_text(COC, {}, "", data={"weapons": [HAMMER]})
    p = house_rules.parse_text(COC, text, notes_max=800)
    assert p.ok and p.data["weapons"][0]["name"] == "雷霆之锤"


def test_house_rules_v1_file_still_imports():
    p = house_rules.parse_text(
        COC,
        "format: coc-player/house-rules\nformat_version: 1\nrule_system: coc\n"
        "options: {critical_max: 3}\n",
        notes_max=800,
    )
    assert p.ok and p.options == {"critical_max": 3} and p.data == {}


def test_house_rules_data_errors_and_unknown_tables():
    p = house_rules.parse_text(
        COC,
        "format: coc-player/house-rules\nformat_version: 2\nrule_system: coc\n"
        "data: {weapons: [{name: 坏枪}], spells: [{name: x}]}\n",
        notes_max=800,
    )
    assert not p.ok and any("坏枪" in e for e in p.errors)
    assert any("spells" in w for w in p.warnings)


# ── 接口 ─────────────────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'api.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine)

    def override():
        s = maker()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override
    app.dependency_overrides[require_local_client] = lambda: None
    yield TestClient(app), maker
    app.dependency_overrides.clear()


def test_api_village_table_edit_roundtrip(client):
    c, _ = client
    view = c.get("/api/rulebooks/village-rules/coc/data/weapons").json()
    assert len(view["rows"]) == 106 and set(view["status"].values()) == {"official"}

    rows = [r for r in view["rows"] if r["name"] != "长鞭"] + [HAMMER]
    saved = c.put("/api/rulebooks/village-rules/coc/data/weapons", json={"rows": rows}).json()
    assert saved["status"]["雷霆之锤"] == "added"
    assert [d["name"] for d in saved["deleted"]] == ["长鞭"]

    # 建卡接口立刻看得到
    names = [w["name"] for w in c.get("/api/rules/coc/weapons").json()["weapons"]]
    assert "雷霆之锤" in names and "长鞭" not in names


def test_api_village_table_rejects_bad_rows_with_reasons(client):
    c, _ = client
    r = c.put("/api/rulebooks/village-rules/coc/data/weapons", json={"rows": [{"name": "坏枪"}]})
    assert r.status_code == 400 and "坏枪" in r.json()["detail"]
    assert c.get("/api/rulebooks/village-rules/coc/data/nope").status_code == 404


def test_api_module_table_and_chargen_with_module(client):
    c, maker = client
    db = maker()
    m = Module(title="1890", rule_system="coc")
    db.add(m)
    db.commit()
    mid = m.id
    db.close()
    occ = {"name": "电报员", "category": "维多利亚", "skill_formula": "EDU*4", "skills": ["电子学"]}
    rows = c.get(f"/api/modules/{mid}/rule-data/occupations").json()["rows"] + [occ]
    assert c.put(f"/api/modules/{mid}/rule-data/occupations", json={"rows": rows}).status_code == 200

    plain = [o["name"] for o in c.get("/api/rules/coc/occupations").json()]
    with_module = [o["name"] for o in c.get(f"/api/rules/coc/occupations?module_id={mid}").json()]
    assert "电报员" not in plain and "电报员" in with_module


def test_table_view_carries_originals_of_modified_rows():
    view = rule_options_service.table_view("coc", "weapons", [{"name": "弓箭", "dam": "1D8"}, HAMMER])
    assert view["status"]["弓箭"] == "modified" and view["originals"]["弓箭"]["dam"] == "1D6+半DB"
    assert "雷霆之锤" not in view["originals"]


# ── 规则页的统一保存：参数、约定、开关与整张表一次提交 ─────────────────────


def test_unified_save_writes_options_and_tables_together(client):
    c, _ = client
    rows = list(c.get("/api/rulebooks/village-rules/coc/data/weapons").json()["rows"]) + [HAMMER]
    r = c.put("/api/rulebooks/village-rules/coc", json={
        "options": {"critical_max": 4}, "table_notes": "重调查", "enabled": True,
        "tables": {"weapons": rows},
    })
    assert r.status_code == 200
    body = r.json()
    assert body["options"] == {"critical_max": 4}
    assert body["data"]["weapons"][0]["name"] == "雷霆之锤"
    assert "武器表：新增 1" in body["labels"]


def test_unified_save_is_all_or_nothing(client):
    c, _ = client
    r = c.put("/api/rulebooks/village-rules/coc", json={
        "options": {"critical_max": 4}, "tables": {"weapons": [{"name": "坏枪"}]},
    })
    assert r.status_code == 400 and "武器表" in r.json()["detail"]
    saved = c.get("/api/rulebooks/village-rules/coc").json()
    assert saved["options"] == {} and saved["data"] == {}


def test_unified_save_leaves_unlisted_tables_alone(client):
    c, _ = client
    skills = c.get("/api/rulebooks/village-rules/coc/data/skills").json()["rows"] + [{"name": "无线电", "base": 10}]
    c.put("/api/rulebooks/village-rules/coc", json={"options": {}, "tables": {"skills": skills}})
    weapons = c.get("/api/rulebooks/village-rules/coc/data/weapons").json()["rows"] + [HAMMER]
    c.put("/api/rulebooks/village-rules/coc", json={"options": {}, "tables": {"weapons": weapons}})
    data = c.get("/api/rulebooks/village-rules/coc").json()["data"]
    assert set(data) == {"skills", "weapons"}


def test_import_preview_returns_full_tables(client):
    c, _ = client
    text = house_rules.export_text(COC, {}, "", data={"weapons": [HAMMER]})
    p = c.post("/api/rulebooks/village-rules/coc/import-preview", json={"text": text}).json()
    assert p["ok"] and len(p["tables"]["weapons"]) == 107 and len(p["tables"]["skills"]) == 51
