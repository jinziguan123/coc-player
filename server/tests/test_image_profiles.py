"""生图配置独立化：旧文件迁移、CRUD/激活，以及「生图与文本互不牵连」的契约。"""

import json

from fastapi.testclient import TestClient

from app.api import ai_settings
from app.main import app


def _write(tmp_path, data: dict):
    f = tmp_path / "ai_settings.json"
    f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return f


# ── 旧文件迁移 ──────────────────────────────────────────────

def test_migration_dedupes_shared_comfyui(monkeypatch, tmp_path):
    """同一台 ComfyUI 过去要在每个文本配置里各抄一份，迁移必须合并成一条。

    取自真实配置：四条文本配置里两条填着同一个 ComfyUI 地址、一条填 OpenAI 生图模型。
    """
    monkeypatch.setattr(ai_settings, "SETTINGS_FILE", _write(tmp_path, {"profiles": [
        {"id": "1", "name": "deepseek-pro", "api_key": "k1", "is_active": False,
         "image_backend": "comfyui", "comfyui_base_url": "http://172.30.18.236:8188"},
        {"id": "2", "name": "小米MIMO", "api_key": "k2", "is_active": False},
        {"id": "3", "name": "lucen", "api_key": "k3", "is_active": False,
         "image_backend": "openai", "image_model": "gpt-image-2"},
        {"id": "4", "name": "deepseek-flash", "api_key": "k4", "is_active": True,
         "image_backend": "comfyui", "comfyui_base_url": "http://172.30.18.236:8188"},
    ]}))

    images = ai_settings._load_image_profiles()
    assert len(images) == 2                       # 两条 ComfyUI 合并为一
    comfy = next(p for p in images if p.backend == "comfyui")
    openai = next(p for p in images if p.backend == "openai")
    assert comfy.comfyui_base_url == "http://172.30.18.236:8188"
    assert openai.model == "gpt-image-2"
    # 原本激活的文本配置用 ComfyUI 出图 → 迁移后仍用它，升级前后出图行为不变
    assert comfy.is_active is True and openai.is_active is False


def test_migration_inherits_text_base_and_key(monkeypatch, tmp_path):
    """旧语义「生图地址/密钥留空即复用文本配置的」必须在迁移时就地固化——
    拆开后没有文本配置可回落，不解析就等于把配置弄丢。"""
    monkeypatch.setattr(ai_settings, "SETTINGS_FILE", _write(tmp_path, {"profiles": [
        {"id": "1", "name": "A", "base_url": "https://one.example/v1", "api_key": "sk-text",
         "is_active": True, "image_backend": "openai", "image_model": "dall-e-3"},
    ]}))

    p = ai_settings._load_image_profiles()[0]
    assert p.base_url == "https://one.example/v1"
    assert p.api_key == "sk-text"


def test_migration_keeps_dedicated_image_credentials(monkeypatch, tmp_path):
    """独立填了生图地址/密钥的，迁移后要保留它自己的那份，别被文本配置盖掉。"""
    monkeypatch.setattr(ai_settings, "SETTINGS_FILE", _write(tmp_path, {"profiles": [
        {"id": "1", "name": "A", "base_url": "https://chat.example/v1", "api_key": "sk-text",
         "is_active": True, "image_backend": "openai", "image_model": "dall-e-3",
         "image_base_url": "https://img.example/v1", "image_api_key": "sk-img"},
    ]}))

    p = ai_settings._load_image_profiles()[0]
    assert p.base_url == "https://img.example/v1"
    assert p.api_key == "sk-img"


def test_migration_is_idempotent_and_skips_unconfigured(monkeypatch, tmp_path):
    """没配过生图的旧文件迁出空列表，且只迁一次——之后用户删光了也不会被重新灌回来。"""
    f = _write(tmp_path, {"profiles": [{"id": "1", "name": "A", "api_key": "k", "is_active": True}]})
    monkeypatch.setattr(ai_settings, "SETTINGS_FILE", f)

    assert ai_settings._load_image_profiles() == []
    assert "image_profiles" in json.loads(f.read_text("utf-8"))   # 迁移标记已落盘

    c = TestClient(app)
    created = c.post("/api/settings/ai/image-profiles", json={"name": "新的"}).json()
    assert len(ai_settings._load_image_profiles()) == 1
    # 再读一次不会因为「文本配置没有生图字段」而把刚建的冲掉
    assert ai_settings._load_image_profiles()[0].id == created["id"]


def test_saving_text_profiles_keeps_image_profiles(monkeypatch, tmp_path):
    """回归：两套配置同住一个 JSON，写文本配置时整份重写会把生图配置抹掉。"""
    monkeypatch.setattr(ai_settings, "SETTINGS_FILE", tmp_path / "ai_settings.json")
    c = TestClient(app)

    c.post("/api/settings/ai/image-profiles", json={"name": "出图", "model": "dall-e-3"})
    c.post("/api/settings/ai/profiles", json={"name": "文本", "model_name": "m", "api_key": "k"})
    assert len(ai_settings._load_image_profiles()) == 1
    assert len(ai_settings._load_profiles()) == 1


# ── CRUD / 激活 ─────────────────────────────────────────────

def test_image_profile_crud_and_activation(monkeypatch, tmp_path):
    monkeypatch.setattr(ai_settings, "SETTINGS_FILE", tmp_path / "ai_settings.json")
    c = TestClient(app)

    a = c.post("/api/settings/ai/image-profiles",
               json={"name": "A", "model": "dall-e-3", "api_key": "sk-verylongsecret1234"}).json()
    assert a["is_active"] is True          # 第一条自动激活
    assert "****" in a["api_key"]          # 响应恒掩码
    assert c.get(f"/api/settings/ai/image-profiles/{a['id']}/key").json()["api_key"] == "sk-verylongsecret1234"

    b = c.post("/api/settings/ai/image-profiles",
               json={"name": "B", "backend": "comfyui", "comfyui_base_url": "http://x:8188"}).json()
    assert b["is_active"] is False

    c.post(f"/api/settings/ai/image-profiles/{b['id']}/activate")
    assert ai_settings.load_active_image_profile().name == "B"

    # 掩码原样回传 = 用户没动这个框 → 保留旧密钥
    c.put(f"/api/settings/ai/image-profiles/{a['id']}", json={"api_key": a["api_key"]})
    assert c.get(f"/api/settings/ai/image-profiles/{a['id']}/key").json()["api_key"] == "sk-verylongsecret1234"

    # 删掉激活项 → 顺位激活剩下的，不会留下「一条都没激活」的悬空状态
    c.delete(f"/api/settings/ai/image-profiles/{b['id']}")
    assert ai_settings.load_active_image_profile().name == "A"

    c.delete(f"/api/settings/ai/image-profiles/{a['id']}")
    assert ai_settings.load_active_image_profile() is None   # 一条不剩＝不出图，合法状态

    assert c.get("/api/settings/ai/image-profiles/nope/key").status_code == 404
    assert c.post("/api/settings/ai/image-profiles/nope/activate").status_code == 404
    assert c.delete("/api/settings/ai/image-profiles/nope").status_code == 404


def test_text_profile_no_longer_carries_image_fields(monkeypatch, tmp_path):
    """文本配置不再接收生图字段——旧前端若还发过来，也只会被忽略，不会悄悄存进去。"""
    monkeypatch.setattr(ai_settings, "SETTINGS_FILE", tmp_path / "ai_settings.json")
    c = TestClient(app)

    created = c.post("/api/settings/ai/profiles", json={
        "name": "A", "model_name": "m", "api_key": "k",
        "image_model": "dall-e-3", "image_backend": "comfyui",
    }).json()
    assert "image_model" not in created or not created["image_model"]
    assert ai_settings._load_image_profiles() == []   # 没有从文本配置里凭空冒出生图配置


def test_get_image_llm_follows_active_profile(monkeypatch, tmp_path):
    """跑团时用哪个生图后端，只看激活的生图配置——与文本模型用什么协议无关。"""
    from app.ai.image_gen import ComfyUIImageGenerator, NullImageGenerator, get_image_llm

    monkeypatch.setattr(ai_settings, "SETTINGS_FILE", tmp_path / "ai_settings.json")
    assert isinstance(get_image_llm(), NullImageGenerator)

    c = TestClient(app)
    c.post("/api/settings/ai/image-profiles",
           json={"name": "本地", "backend": "comfyui", "comfyui_base_url": "http://x:8188"})
    # 文本侧用 Anthropic：过去这会让 OpenAI 生图静默失效，现在互不相干
    c.post("/api/settings/ai/profiles",
           json={"name": "claude", "protocol": "anthropic", "model_name": "claude-x", "api_key": "k"})

    gen = get_image_llm()
    assert isinstance(gen, ComfyUIImageGenerator) and gen.supports_image_gen() is True
