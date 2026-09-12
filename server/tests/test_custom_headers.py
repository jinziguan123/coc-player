"""AI 配置的自定义请求头：Google（x-goog-api-key）、OpenCode 这类要额外头才放行的服务。

每次请求都原样附上，同名覆盖默认头；接口返回时头值按密钥掩码，更新时带掩码的项沿用旧值。
"""

import asyncio
import json

from fastapi.testclient import TestClient

from app.ai import llm_factory, profile_store
from app.ai.provider import clean_headers
from app.ai.providers.anthropic import AnthropicProvider
from app.ai.providers.openai_compat import OpenAICompatProvider
from app.api import ai_settings
from app.main import app


def test_clean_headers_去空白丢空项():
    assert clean_headers({" X-Goog-Api-Key ": " k1 ", "": "v", "Empty": "  ", "Origin": "https://a"}) == {
        "X-Goog-Api-Key": "k1", "Origin": "https://a",
    }
    assert clean_headers(None) == {}


def test_provider_请求头合并且同名覆盖默认():
    p = OpenAICompatProvider(model="m", base_url="http://x", api_key="sk",
                             extra_headers={"x-goog-api-key": "g1", "Authorization": "Token t"})
    h = p._headers()
    assert h["x-goog-api-key"] == "g1"
    assert h["Authorization"] == "Token t"          # 自定义覆盖默认的 Bearer
    assert h["Content-Type"] == "application/json"
    a = AnthropicProvider(model="c", api_key="sk-a", extra_headers={"anthropic-beta": "x"})
    ah = a._headers()
    assert ah["anthropic-beta"] == "x" and ah["x-api-key"] == "sk-a"


def test_工厂把配置里的头透传给两家_provider():
    prof = profile_store.AIProfile(name="g", protocol="openai", model_name="gemini", base_url="https://g",
                                   api_key="k", extra_headers={"x-goog-api-key": "gk"})
    assert llm_factory.provider_from_profile(prof)._headers()["x-goog-api-key"] == "gk"
    prof_a = prof.model_copy(update={"protocol": "anthropic"})
    assert llm_factory.provider_from_profile(prof_a)._headers()["x-goog-api-key"] == "gk"


def test_旧配置文件没有该字段也能加载(monkeypatch, tmp_path):
    f = tmp_path / "ai_settings.json"
    f.write_text(json.dumps({"profiles": [{"id": "1", "name": "旧", "api_key": "k", "is_active": True}]}), "utf-8")
    monkeypatch.setattr(profile_store, "SETTINGS_FILE", f)
    assert profile_store._load_profiles()[0].extra_headers == {}


def test_接口_创建掩码_更新沿用掩码项_删除即删(monkeypatch, tmp_path):
    monkeypatch.setattr(profile_store, "SETTINGS_FILE", tmp_path / "ai_settings.json")
    c = TestClient(app)
    created = c.post("/api/settings/ai/profiles", json={
        "name": "google", "model_name": "gemini", "api_key": "sk-1234567890",
        "extra_headers": {"x-goog-api-key": "AIzaSy-real-secret-value", "x-tenant": "short"},
    }).json()
    assert created["extra_headers"]["x-goog-api-key"] == "AIza****alue"     # 长值首尾各留 4 位
    assert created["extra_headers"]["x-tenant"] == "****"                    # 短值全掩
    assert profile_store._load_profiles()[0].extra_headers["x-goog-api-key"] == "AIzaSy-real-secret-value"
    # 列表也掩码；/key 给明文
    assert c.get("/api/settings/ai/profiles").json()[0]["extra_headers"]["x-tenant"] == "****"
    assert c.get(f"/api/settings/ai/profiles/{created['id']}/key").json()["extra_headers"]["x-tenant"] == "short"
    # 更新：带掩码的项沿用旧值，新项写入，没提交的项删除
    c.put(f"/api/settings/ai/profiles/{created['id']}", json={
        "extra_headers": {"x-goog-api-key": "AIza****alue", "x-new": "n1"},
    })
    stored = profile_store._load_profiles()[0].extra_headers
    assert stored == {"x-goog-api-key": "AIzaSy-real-secret-value", "x-new": "n1"}
    # 复制配置连头一起带走，返回时同样掩码
    dup = c.post(f"/api/settings/ai/profiles/{created['id']}/duplicate").json()
    assert dup["extra_headers"]["x-goog-api-key"] == "AIza****alue"
    assert profile_store._load_profiles()[1].extra_headers["x-new"] == "n1"


class _FakeClient:
    captured: dict = {}

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, headers=None):
        _FakeClient.captured = {"url": url, "headers": dict(headers or {})}

        class _R:
            def raise_for_status(self):
                pass

            def json(self):
                return {"data": [{"id": "gemini-2.5-pro"}]}
        return _R()


def test_模型清单探测也带自定义头(monkeypatch):
    monkeypatch.setattr(ai_settings.httpx, "AsyncClient", _FakeClient)
    res = asyncio.run(ai_settings.list_upstream_models(ai_settings.ModelsProbe(
        protocol="openai", base_url="https://g/v1beta/openai", api_key="k",
        extra_headers={"x-goog-api-key": "gk"},
    )))
    assert res.success and res.models == ["gemini-2.5-pro"]
    assert _FakeClient.captured["headers"]["x-goog-api-key"] == "gk"
    assert _FakeClient.captured["headers"]["Authorization"] == "Bearer k"


# ── 连接测试的健壮性（Gemini 实测踩出来的两个坑） ──────────────────────────

class _Resp:
    def __init__(self, payload, status=200, text=""):
        self._payload, self.status_code, self.text = payload, status, text or ""

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


class _PostClient:
    def __init__(self, resp):
        self.resp = resp

    async def post(self, url, headers=None, json=None):
        return self.resp


def _profile(**kw):
    base = {"name": "t", "protocol": "openai", "model_name": "m", "api_key": "k", "base_url": "https://x"}
    return profile_store.AIProfile(**{**base, **kw})


def test_思考吃光预算时仍判连接成功():
    """Gemini 实测：max_tokens 小时思考把预算花完，message 里只剩一个 role。
    此前 data["choices"][0]["message"]["content"] 直接下标 → KeyError，界面上是一句 'content'。"""
    payload = {
        "choices": [{"finish_reason": "length", "message": {"role": "assistant"}}],
        "usage": {"completion_tokens_details": {"reasoning_tokens": 61}},
    }
    msg = asyncio.run(ai_settings._test_openai(_PostClient(_Resp(payload)), _profile()))
    assert msg.startswith("连接成功") and "61" in msg and "思考" in msg


def test_正常返回正文时照常显示():
    payload = {"choices": [{"message": {"role": "assistant", "content": " OK "}}]}
    assert asyncio.run(ai_settings._test_openai(_PostClient(_Resp(payload)), _profile())) == "连接成功: OK"


def test_被长度截断与空内容各有说法():
    cut = {"choices": [{"finish_reason": "length", "message": {"role": "assistant", "content": ""}}]}
    assert "截断" in asyncio.run(ai_settings._test_openai(_PostClient(_Resp(cut)), _profile()))
    empty = {"choices": [{"message": {"role": "assistant", "content": ""}}]}
    assert "空内容" in asyncio.run(ai_settings._test_openai(_PostClient(_Resp(empty)), _profile()))


def test_anthropic_没有文本块也不抛():
    ok = {"content": [{"type": "text", "text": "OK"}]}
    assert asyncio.run(ai_settings._test_anthropic(_PostClient(_Resp(ok)), _profile())) == "连接成功: OK"
    # 只有思考块、没有文本块
    thinking = {"content": [{"type": "thinking", "thinking": "..."}]}
    assert "没有返回文本" in asyncio.run(ai_settings._test_anthropic(_PostClient(_Resp(thinking)), _profile()))


def test_错误体是数组也能取出那句人话():
    """Gemini 的错误响应是数组 [{"error": {...}}]，只认字典会把整段 JSON 糊到界面上。"""
    assert ai_settings._error_message([{"error": {"message": "Missing or invalid Authorization header."}}]) == \
        "Missing or invalid Authorization header."
    assert ai_settings._error_message({"error": {"message": "boom"}}) == "boom"
    assert ai_settings._error_message([]) == ""
    assert ai_settings._error_message("纯文本") == ""
