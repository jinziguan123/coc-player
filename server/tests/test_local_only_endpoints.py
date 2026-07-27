"""管理本机资产的端点仅限房主本机。

背景：客人模式下前端整体切到房主地址，于是客人的设置页显示的是**房主的** AI 配置，
「显示密钥」能直接读出房主的 API key；素材库的删除同理打在房主身上。
ADR-001 的「可信局域网」是指相信朋友不捣乱，不该延伸到「默认把凭据给朋友看」。
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import net_access


@pytest.fixture(autouse=True)
def _lan_open(tmp_path, monkeypatch):
    """打开局域网可达，好让请求越过来源闸、真正走到本机限制这一层。"""
    monkeypatch.setattr(net_access, "SETTINGS_FILE", tmp_path / "net_settings.json")
    net_access.reset_cache()
    net_access.set_lan_enabled(True)
    yield
    net_access.reset_cache()


def _guest() -> TestClient:
    """模拟一个已经连进来的局域网客人。"""
    return TestClient(app, client=("192.168.1.50", 5555))


def _host() -> TestClient:
    return TestClient(app, client=("127.0.0.1", 5555))


# 端点，请求体（None 表示不带体）
CREDENTIAL_ENDPOINTS = [
    ("get", "/api/settings/ai/profiles", None),
    ("get", "/api/settings/ai/profiles/whatever/key", None),
    ("post", "/api/settings/ai/profiles", {}),
    ("delete", "/api/settings/ai/profiles/whatever", None),
    ("post", "/api/settings/ai/profiles/whatever/activate", None),
]

DESTRUCTIVE_ENDPOINTS = [
    ("delete", "/api/characters/whatever", None),
    ("put", "/api/characters/whatever", {}),
    ("delete", "/api/modules/whatever", None),
    ("post", "/api/modules/whatever/images/regenerate", {}),
    ("post", "/api/modules/whatever/rag/rebuild", None),
    ("delete", "/api/rulebooks/whatever", None),
    ("post", "/api/onboarding/start", {}),
]


@pytest.mark.parametrize(
    ("verb", "path", "body"), CREDENTIAL_ENDPOINTS + DESTRUCTIVE_ENDPOINTS,
)
def test_guest_is_rejected(verb, path, body):
    r = getattr(_guest(), verb)(path, **({"json": body} if body is not None else {}))
    assert r.status_code == 403, f"{verb.upper()} {path} 未拦住客人"
    assert "本机" in r.json()["detail"]


def test_host_is_not_blocked_by_this_guard():
    """房主本机不受影响——只验证没被这道守卫挡掉，不关心业务结果。"""
    r = _host().get("/api/settings/ai/status")
    assert r.status_code == 200

    # 不存在的资源应当是 404 之类的业务错误，而不是 403
    r = _host().delete("/api/rulebooks/whatever")
    assert r.status_code != 403


def test_ai_status_probe_stays_open_to_guests():
    """例外：客人要判断的正是「房主配没配好 AI」——AI 调用在房主机器上跑。
    该探针只返回布尔与配置昵称，不含凭据。"""
    r = _guest().get("/api/settings/ai/status")
    assert r.status_code == 200
    assert set(r.json()) <= {"configured", "name"}


def test_reads_of_shared_libraries_stay_open_to_guests():
    """只收紧「管理」，不收紧「查看」——客人仍要能看房间用到的模组/角色。"""
    for path in ("/api/modules", "/api/characters", "/api/rulebooks"):
        assert _guest().get(path).status_code != 403, path


# 客人在房间内的合法动作：把自己的角色带进房主的库、在大厅里 AI 生成角色入座。
# 锁掉这两个，客人就根本无法入座。
GUEST_JOIN_FLOW = [
    ("post", "/api/characters", {}),
    ("post", "/api/characters/ai-generate", {}),
    ("post", "/api/characters/evaluate", {}),
]


@pytest.mark.parametrize(("verb", "path", "body"), GUEST_JOIN_FLOW)
def test_guest_join_flow_not_blocked(verb, path, body):
    r = getattr(_guest(), verb)(path, json=body)
    assert r.status_code != 403, f"{verb.upper()} {path} 挡住了客人入座流程"
