"""P2：组件参数 schema、摘要标签、房规文件的导出/导入预览，以及对应接口。"""

from __future__ import annotations

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.deps import require_local_client
from app.database import get_db
from app.main import app
from app.models import Base
from app.rules import get_system, house_rules, ruleset_for
from app.services import rule_options_service

COC = get_system("coc")


def _labels(raw: dict) -> list[str]:
    return ruleset_for("coc", raw).labels()


# ── 摘要标签（原先写死在前端 lib/villageRules.ts，迁到各组件后行为不变）──────


def test_labels_only_list_changes():
    assert _labels({"critical_max": 3}) == ["大成功 ≤ 3"]
    assert _labels({}) == []


def test_luck_spend_details_merge_into_one_label():
    assert _labels({
        "luck_spend": True, "luck_spend_max": 20, "luck_spend_in_combat": False,
    }) == ["幸运消费（单次 ≤ 20 点、战斗中不可用）"]


def test_unofficial_reroll_is_marked():
    assert "本桌自定" in _labels({"luck_reroll": True, "luck_reroll_cost": 10})[0]


def test_labels_follow_preset_order():
    assert _labels({
        "improvement": False, "major_wound_divisor": 3, "critical_max": 5, "dice_pool_cap": 0,
    }) == ["大成功 ≤ 5", "不使用奖惩骰", "重伤线 1/3", "不做成长检定"]


# ── manifest ─────────────────────────────────────────────────────────────


def test_manifest_lists_configurable_components_with_schema():
    m = COC.manifest()
    panel = [c for c in m["components"] if c["group"]]
    assert [c["id"] for c in panel] == [
        "coc7.check", "coc7.luck-spend", "coc7.luck-reroll",
        "coc7.wound", "coc7.sanity", "coc7.growth",
    ]
    check = panel[0]["config_schema"]["properties"]
    assert check["critical_max"]["minimum"] == 1 and check["critical_max"]["maximum"] == 20
    assert [c["value"] for c in check["fumble_rule"]["x-choices"]] == [
        "raw", "hundred_only", "ninety_six_plus",
    ]
    sanity = panel[4]["config_schema"]["properties"]
    assert sanity["insanity_flat_threshold"]["x-show-if"] == {"insanity_rule": "flat"}
    assert {c["toggle"] for c in panel} == {None, "luck_spend", "luck_reroll", "improvement"}


def test_every_option_appears_in_exactly_one_component_schema():
    seen: list[str] = []
    for c in COC.manifest()["components"]:
        if c["config_schema"]:
            seen += list(c["config_schema"]["properties"])
    assert sorted(seen) == sorted(COC.options_model.model_fields)


# ── 严格解析 ─────────────────────────────────────────────────────────────


def test_strict_parse_reports_each_problem_without_fixing():
    diff, errors, unknown = COC.parse_options_strict({
        "critical_max": 99, "fumble_rule": "never", "luck_spend": "yes", "dice_pool_cap": 1.5,
        "mystery": 1,
    })
    assert diff == {}
    assert len(errors) == 4
    assert any("critical_max" in e and "1–20" in e for e in errors)
    assert any("fumble_rule" in e for e in errors)
    assert any("luck_spend" in e and "true 或 false" in e for e in errors)
    assert any("dice_pool_cap" in e and "整数" in e for e in errors)
    assert unknown == ["mystery"]


def test_strict_parse_keeps_only_diff():
    diff, errors, unknown = COC.parse_options_strict({"critical_max": 1, "major_wound_divisor": 3})
    assert (diff, errors, unknown) == ({"major_wound_divisor": 3}, [], [])


def test_lenient_parse_still_clamps():
    assert COC.parse_options({"critical_max": 99}).critical_max == 20


# ── 房规文件 ─────────────────────────────────────────────────────────────


def _preview(text: str) -> house_rules.HouseRulesPreview:
    return house_rules.parse_text(COC, text, notes_max=800)


def test_export_then_import_roundtrip():
    text = house_rules.export_text(COC, {"critical_max": 5, "luck_spend": True}, "重调查轻战斗")
    assert text.startswith("# CoC Player 房规文件")
    doc = yaml.safe_load(text)
    assert doc["format_version"] == 2 and doc["base_preset"] == "coc@1"
    p = _preview(text)
    assert p.ok and p.warnings == []
    assert p.options == {"critical_max": 5, "luck_spend": True}
    assert p.table_notes == "重调查轻战斗"


@pytest.mark.parametrize(("text", "message"), [
    ("just: a yaml", "不是房规文件"),
    ("format: coc-player/house-rules\nformat_version: 99\nrule_system: coc\n", "更新版本"),
    ("format: coc-player/house-rules\nformat_version: 1\nrule_system: dnd\n", "给「dnd」的"),
    ("format: [unclosed", "不是合法的 YAML"),
    ("format: coc-player/house-rules\nformat_version: 1\nrule_system: coc\n"
     "options: {critical_max: 0}\n", "critical_max"),
    ("format: coc-player/house-rules\nformat_version: 1\nrule_system: coc\n"
     "table_notes: " + "长" * 801 + "\n", "超过 800 字"),
])
def test_import_errors_are_readable(text, message):
    p = _preview(text)
    assert not p.ok
    assert any(message in e for e in p.errors), p.errors


def test_import_warns_but_accepts_unknown_parameters():
    p = _preview(
        "format: coc-player/house-rules\nformat_version: 1\nrule_system: coc\n"
        "base_preset: coc@0\nextra: 1\noptions: {critical_max: 5, pulp_talents: true}\n",
    )
    assert p.ok and p.options == {"critical_max": 5}
    text = " ".join(p.warnings)
    assert "pulp_talents" in text and "coc@0" in text and "extra" in text


def test_import_never_constructs_python_objects():
    p = _preview("format: !!python/object/apply:os.system ['echo hi']\n")
    assert not p.ok and "不是合法的 YAML" in p.errors[0]


def test_oversized_file_rejected():
    assert "KB" in _preview("x" * (house_rules.MAX_BYTES + 1)).errors[0]


# ── 接口 ─────────────────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'p2.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)

    def override_get_db():
        db = session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[require_local_client] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_api_manifest(client):
    r = client.get("/api/rulebooks/village-rules/coc/manifest")
    assert r.status_code == 200
    assert r.json()["house_rules_format_version"] == 2
    assert client.get("/api/rulebooks/village-rules/dnd/manifest").status_code == 404


def test_api_read_returns_backend_labels(client):
    client.put("/api/rulebooks/village-rules/coc", json={"options": {"critical_max": 3}})
    assert client.get("/api/rulebooks/village-rules/coc").json()["labels"] == ["大成功 ≤ 3"]


def test_api_export_preview_then_save(client):
    client.put("/api/rulebooks/village-rules/coc", json={
        "options": {"major_wound_divisor": 3}, "table_notes": "NPC 死了就是死了",
    })
    exported = client.get("/api/rulebooks/village-rules/coc/export").json()
    assert exported["filename"] == "house-rules-coc.yaml"

    # 清空后再导入：预览不落库
    client.put("/api/rulebooks/village-rules/coc", json={"options": {}, "table_notes": ""})
    preview = client.post(
        "/api/rulebooks/village-rules/coc/import-preview", json={"text": exported["text"]},
    ).json()
    assert preview["ok"] and preview["labels"] == ["重伤线 1/3"]
    assert client.get("/api/rulebooks/village-rules/coc").json()["options"] == {}

    # 房主确认 → 走原有 PUT 保存
    client.put("/api/rulebooks/village-rules/coc", json={
        "options": preview["options"], "table_notes": preview["table_notes"],
    })
    saved = client.get("/api/rulebooks/village-rules/coc").json()
    assert saved["options"] == {"major_wound_divisor": 3}
    assert saved["table_notes"] == "NPC 死了就是死了"


def test_service_labels_ignore_master_switch(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 's.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    rule_options_service.save_village_options(db, "coc", {"critical_max": 4}, enabled=False)
    assert rule_options_service.village_labels(db, "coc") == ["大成功 ≤ 4"]


def test_web_manifest_fixture_in_sync():
    """前端村规面板的单测用一份真实 manifest 做夹具；规则组件一改，这份夹具必须跟着重导，
    否则前端测的是一份过期的 schema。重导：

        cd server && .venv/bin/python -c "import json; from app.rules import get_system; \\
        print(json.dumps(get_system('coc').manifest(), ensure_ascii=False, indent=2))" \\
        > ../apps/web/src/components/game/__fixtures__/coc-manifest.json
    """
    import json
    from pathlib import Path

    fixture = (
        Path(__file__).resolve().parents[2]
        / "apps/web/src/components/game/__fixtures__/coc-manifest.json"
    )
    assert json.loads(fixture.read_text(encoding="utf-8")) == COC.manifest()


def test_export_lists_options_in_panel_order():
    text = house_rules.export_text(COC, {"improvement": False, "critical_max": 5, "luck_spend": True}, "")
    assert list(yaml.safe_load(text)["options"]) == ["critical_max", "luck_spend", "improvement"]
