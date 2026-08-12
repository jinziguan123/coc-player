"""开局前置校验：AI 配置状态端点 + LLM 错误归类。"""

import httpx
from fastapi.testclient import TestClient

from app.api import ai_settings
from app.main import app
from app.services.chat_service import _classify_llm_error


def test_ai_status_reports_configured(monkeypatch):
    c = TestClient(app)

    # 无激活配置 → 未就绪
    monkeypatch.setattr(ai_settings, "load_active_profile", lambda: None)
    assert c.get("/api/settings/ai/status").json()["configured"] is False

    # 有激活配置但缺 key → 未就绪
    monkeypatch.setattr(
        ai_settings, "load_active_profile",
        lambda: ai_settings.AIProfile(name="x", model_name="m", api_key=""),
    )
    assert c.get("/api/settings/ai/status").json()["configured"] is False

    # 有 key + 模型名 → 就绪
    monkeypatch.setattr(
        ai_settings, "load_active_profile",
        lambda: ai_settings.AIProfile(name="主配置", model_name="deepseek-chat", api_key="sk-x"),
    )
    body = c.get("/api/settings/ai/status").json()
    assert body["configured"] is True and body["name"] == "主配置"


def test_classify_llm_error_maps_status_and_network():
    def _http_err(code: int) -> httpx.HTTPStatusError:
        req = httpx.Request("POST", "http://x")
        return httpx.HTTPStatusError("e", request=req, response=httpx.Response(code, request=req))

    assert "API Key" in _classify_llm_error(_http_err(401))
    assert "限流" in _classify_llm_error(_http_err(429))
    assert _classify_llm_error(_http_err(500))
    assert "连接" in _classify_llm_error(httpx.ConnectError("boom"))
    assert _classify_llm_error(ValueError("其它")) == ""  # 无法归类 → 空串回落通用文案


def test_set_fast_profile_toggle(monkeypatch, tmp_path):
    """快模型标记：单选（设 A 清 B）、重复点同一个即取消；load_fast_profile 读取一致。"""
    c = TestClient(app)
    monkeypatch.setattr(ai_settings, "SETTINGS_FILE", tmp_path / "ai_settings.json")

    a = c.post("/api/settings/ai/profiles", json={"name": "A", "model_name": "m1", "api_key": "k1"}).json()
    b = c.post("/api/settings/ai/profiles", json={"name": "B", "model_name": "m2", "api_key": "k2"}).json()

    assert c.post(f"/api/settings/ai/profiles/{a['id']}/set-fast").json()["is_fast"] is True
    assert ai_settings.load_fast_profile().name == "A"

    # 换标 B → A 被清
    c.post(f"/api/settings/ai/profiles/{b['id']}/set-fast")
    assert ai_settings.load_fast_profile().name == "B"

    # 重复点 B → 取消标记，回落主模型（load_fast_profile None）
    resp = c.post(f"/api/settings/ai/profiles/{b['id']}/set-fast").json()
    assert resp["is_fast"] is False and ai_settings.load_fast_profile() is None

    assert c.post("/api/settings/ai/profiles/nonexistent/set-fast").status_code == 404


def test_set_vision_profile_toggle(monkeypatch, tmp_path):
    """视觉模型标记：单选、可取消，与快模型互不干扰（两个槽位各自独立）。"""
    c = TestClient(app)
    monkeypatch.setattr(ai_settings, "SETTINGS_FILE", tmp_path / "ai_settings.json")

    a = c.post("/api/settings/ai/profiles", json={"name": "A", "model_name": "m1", "api_key": "k1"}).json()
    b = c.post("/api/settings/ai/profiles", json={"name": "B", "model_name": "qwen-vl-max", "api_key": "k2"}).json()

    assert c.post(f"/api/settings/ai/profiles/{b['id']}/set-vision").json()["is_vision"] is True
    assert ai_settings.load_vision_profile().name == "B"

    # 两个槽位互不干扰：A 标快模型不影响 B 的视觉标记
    c.post(f"/api/settings/ai/profiles/{a['id']}/set-fast")
    assert ai_settings.load_fast_profile().name == "A"
    assert ai_settings.load_vision_profile().name == "B"

    # 重复点 B → 取消，回落主模型
    resp = c.post(f"/api/settings/ai/profiles/{b['id']}/set-vision").json()
    assert resp["is_vision"] is False and ai_settings.load_vision_profile() is None

    assert c.post("/api/settings/ai/profiles/nonexistent/set-vision").status_code == 404


def test_vision_llm_routes_to_vision_slot(monkeypatch, tmp_path):
    """带团用纯文本主模型时，看图仍走视觉槽位——这正是本槽位存在的理由。"""
    from app.ai import llm_factory

    c = TestClient(app)
    monkeypatch.setattr(ai_settings, "SETTINGS_FILE", tmp_path / "ai_settings.json")
    c.post("/api/settings/ai/profiles", json={"name": "带团", "model_name": "deepseek-chat", "api_key": "k1"})
    vis = c.post(
        "/api/settings/ai/profiles",
        json={"name": "看图", "model_name": "qwen-vl-max", "api_key": "k2"},
    ).json()

    # 未标记视觉槽位：回落主模型，纯文本 → 看不了图（与本槽位引入前完全一致）
    assert llm_factory.get_vision_llm().supports_vision() is False

    c.post(f"/api/settings/ai/profiles/{vis['id']}/set-vision")
    assert llm_factory.get_vision_llm().supports_vision() is True
    assert llm_factory.get_llm().supports_vision() is False   # 主模型不受影响


def test_reveal_key_and_duplicate_profile(monkeypatch, tmp_path):
    """列表/增改响应里 key 恒掩码；/key 端点返回明文供「显示/复制」；
    /duplicate 完整拷贝（含真实 key）、命名「X 副本」、不激活不标快。"""
    c = TestClient(app)
    monkeypatch.setattr(ai_settings, "SETTINGS_FILE", tmp_path / "ai_settings.json")

    a = c.post("/api/settings/ai/profiles", json={
        "name": "A", "model_name": "m", "api_key": "sk-verylongsecret1234",
    }).json()
    assert "****" in a["api_key"]  # 响应恒掩码

    real = c.get(f"/api/settings/ai/profiles/{a['id']}/key").json()
    assert real["api_key"] == "sk-verylongsecret1234"

    dup = c.post(f"/api/settings/ai/profiles/{a['id']}/duplicate").json()
    assert dup["name"] == "A 副本"
    assert dup["is_active"] is False and dup["is_fast"] is False
    assert "****" in dup["api_key"]  # 响应仍掩码
    # 但落盘的是真实 key：副本可直接使用
    assert c.get(f"/api/settings/ai/profiles/{dup['id']}/key").json()["api_key"] == "sk-verylongsecret1234"

    assert c.get("/api/settings/ai/profiles/nope/key").status_code == 404
    assert c.post("/api/settings/ai/profiles/nope/duplicate").status_code == 404


def test_update_image_profile_persists_comfyui_fields(monkeypatch, tmp_path):
    """回归：PUT 更新必须应用 backend/comfyui_* 三字段（此前模型收了、应用漏了，静默丢弃）。

    生图配置独立后这几个字段搬到了 image-profiles，原来的 bug 形态在新端点上同样可能复发。
    """
    c = TestClient(app)
    monkeypatch.setattr(ai_settings, "SETTINGS_FILE", tmp_path / "ai_settings.json")

    p = c.post("/api/settings/ai/image-profiles", json={"name": "本地出图"}).json()
    r = c.put(f"/api/settings/ai/image-profiles/{p['id']}", json={
        "name": "本地出图",
        "backend": "comfyui",
        "comfyui_base_url": "http://172.30.18.236:8188",
        "comfyui_workflow": '{"1": {}}',
    })
    assert r.status_code == 200, r.text
    saved = ai_settings._load_image_profiles()[0]
    assert saved.backend == "comfyui"
    assert saved.comfyui_base_url == "http://172.30.18.236:8188"
    assert saved.comfyui_workflow == '{"1": {}}'


def test_视觉槽位即视觉能力(monkeypatch, tmp_path):
    """把配置放进视觉槽位，本身就是断言「这个模型会看图」——不该再要求勾一次 vision 复选框。

    名字里没有 -vl 的多模态模型（qwen3.7-plus、gpt-5 这类）会被名字启发式判成纯文本，
    于是出现「我明明设了视觉模型」却报「没有可用于看图的模型」。
    """
    from app.ai import llm_factory

    c = TestClient(app)
    monkeypatch.setattr(ai_settings, "SETTINGS_FILE", tmp_path / "ai_settings.json")
    c.post("/api/settings/ai/profiles", json={"name": "带团", "model_name": "deepseek-chat", "api_key": "k1"})
    vis = c.post("/api/settings/ai/profiles", json={
        "name": "看图", "model_name": "qwen3.7-plus", "api_key": "k2",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    }).json()

    assert vis.get("vision") is not True                       # 能力位没勾
    assert llm_factory.get_vision_llm().supports_vision() is False   # 槽位未设 → 回落主模型

    c.post(f"/api/settings/ai/profiles/{vis['id']}/set-vision")
    assert llm_factory.get_vision_llm().supports_vision() is True    # 设了槽位即认可
    assert llm_factory.get_llm().supports_vision() is False          # 主模型不受影响
