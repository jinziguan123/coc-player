"""「获取上游可用模型」：填好地址密钥就能问出清单，问不到时把话说明白。

模型名此前只能手打，差一个横杠就是 404——而报错要等到真开团、KP 该说话的时候才冒出来。
这个端点的价值全在「问不到的时候说人话」上：中转站不实现 `/models` 是常态而非故障，
照直说「手填吧」，别让人以为是自己地址填错了。
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app, client=("127.0.0.1", 1))


class _FakeAsyncClient:
    """顶掉 httpx.AsyncClient：记下请求，按预置的响应作答。"""

    captured: dict = {}

    def __init__(self, respond):
        self._respond = respond

    def __call__(self, *_args, **_kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get(self, url, headers=None):
        _FakeAsyncClient.captured = {"url": url, "headers": headers or {}}
        return self._respond()


def _patch(monkeypatch, respond):
    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient(respond))


def _ok(payload):
    def respond():
        return httpx.Response(200, json=payload, request=httpx.Request("GET", "http://x"))
    return respond


def _status(code: int, body: str = "{}"):
    def respond():
        res = httpx.Response(
            code, content=body.encode(), request=httpx.Request("GET", "http://x"),
        )
        res.raise_for_status()
        return res
    return respond


def test_openai_兼容协议按_base_url_直接拼_models(monkeypatch):
    """base_url 就是 API 根，该带 /v1 的用户自己带——与连接测试同一口径。"""
    _patch(monkeypatch, _ok({"data": [{"id": "gpt-5"}, {"id": "gpt-4o"}]}))
    res = client.post("/api/settings/ai/models", json={
        "protocol": "openai", "base_url": "https://lucen.cc/v1/", "api_key": "sk-x",
    })
    assert res.json()["models"] == ["gpt-4o", "gpt-5"]        # 排过序，好找
    assert _FakeAsyncClient.captured["url"] == "https://lucen.cc/v1/models"
    assert _FakeAsyncClient.captured["headers"]["Authorization"] == "Bearer sk-x"


def test_anthropic_协议自己补_v1_并用_x_api_key(monkeypatch):
    _patch(monkeypatch, _ok({"data": [{"id": "claude-opus-5"}]}))
    res = client.post("/api/settings/ai/models", json={
        "protocol": "anthropic", "base_url": "https://api.anthropic.com", "api_key": "sk-a",
    })
    assert res.json()["models"] == ["claude-opus-5"]
    assert _FakeAsyncClient.captured["url"] == "https://api.anthropic.com/v1/models"
    headers = _FakeAsyncClient.captured["headers"]
    assert headers["x-api-key"] == "sk-a"
    assert headers["anthropic-version"] == "2023-06-01"


@pytest.mark.parametrize("payload", [
    {"data": [{"id": "a"}, {"id": "b"}]},          # 标准形状
    [{"id": "a"}, {"id": "b"}],                     # 有的中转站直接回数组
    ["a", "b"],                                     # 更省事的中转站回字符串数组
    {"data": [{"id": "a"}, {"id": "a"}, {"id": "b"}]},   # 重复的收敛掉
])
def test_几种返回形状都认(monkeypatch, payload):
    """宽容一点，总好过让用户对着「空清单」猜是谁的问题。"""
    _patch(monkeypatch, _ok(payload))
    res = client.post("/api/settings/ai/models", json={"base_url": "https://x/v1"})
    assert res.json()["models"] == ["a", "b"]


@pytest.mark.parametrize("code", [404, 405, 501])
def test_服务没有这个接口时明说让人手填(monkeypatch, code):
    """中转站不实现 /models 是常态，不是故障。这句话说不清楚，用户会去查自己的地址。"""
    _patch(monkeypatch, _status(code))
    body = client.post("/api/settings/ai/models", json={"base_url": "https://x/v1"}).json()
    assert body["success"] is False
    assert "手动填写" in body["message"]


def test_密钥不对时转述上游的说法(monkeypatch):
    _patch(monkeypatch, _status(401, '{"error": {"message": "invalid api key"}}'))
    body = client.post("/api/settings/ai/models", json={"base_url": "https://x/v1"}).json()
    assert body["success"] is False
    assert "invalid api key" in body["message"]


def test_超时不抛异常而是回一句可读的话(monkeypatch):
    def boom():
        raise httpx.TimeoutException("timed out")
    _patch(monkeypatch, boom)
    body = client.post("/api/settings/ai/models", json={"base_url": "https://x/v1"}).json()
    assert body == {"success": False, "models": [], "message": "连接超时"}


def test_空清单也算问不到(monkeypatch):
    _patch(monkeypatch, _ok({"data": []}))
    body = client.post("/api/settings/ai/models", json={"base_url": "https://x/v1"}).json()
    assert body["success"] is False
    assert "手动填写" in body["message"]


def test_只有房主本机能问(monkeypatch):
    """这个端点会拿着用户的密钥去访问用户给的地址，和整个 /api/settings 一样限本机。"""
    _patch(monkeypatch, _ok({"data": [{"id": "a"}]}))
    guest = TestClient(app, client=("192.168.1.50", 5555))
    assert guest.post("/api/settings/ai/models", json={"base_url": "https://x/v1"}).status_code == 403
