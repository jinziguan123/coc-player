"""模组原文索引的重建接口：没有原文时说清原因并清掉误留的失败状态；任务同步登记进「解析进度」。"""
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import database
from app.api import modules as modules_api
from app.main import app
from app.models.module import Module
from app.services import module_rag_service, task_center


class FakeEmbedder:
    model_name, dim = "fake-rebuild", 4

    def embed_passages(self, texts):
        return [np.ones(4).tolist() for _ in texts]

    def embed_query(self, text):
        return np.ones(4).tolist()


@pytest.fixture
def client(monkeypatch):
    # modules 若在别的用例中途才首次导入，手里的 SessionLocal 是那个用例的临时库：显式指向本用例的
    monkeypatch.setattr(modules_api, "SessionLocal", database.SessionLocal)
    monkeypatch.setattr(module_rag_service, "get_embedder", lambda: FakeEmbedder())
    return TestClient(app, client=("127.0.0.1", 5000))


def _module(raw, rag_status=""):
    with database.SessionLocal() as db:
        m = Module(title="雾港失灯事件", rule_system="coc", raw_content=raw, scenes=[], npcs=[], clues=[],
                   rag_status=rag_status)
        db.add(m)
        db.commit()
        return m.id


def test_没有原文的模组重建时说明原因并清掉失败状态(client):
    mid = _module("本模组为 CoC Player 项目原创新手教学示例内容。", rag_status="failed")
    r = client.post(f"/api/modules/{mid}/rag/rebuild")
    assert r.status_code == 400 and "没有可建索引的原文" in r.json()["detail"]
    with database.SessionLocal() as db:
        assert db.get(Module, mid).rag_status == ""
    assert task_center.tasks() == []


def test_重建索引的任务在请求内同步登记(client, monkeypatch):
    mid = _module("第一章 灯塔\n" + "雾港的灯塔已经熄灭了三天。" * 40)
    seen_at_response = []
    original = modules_api._rag_index_bg

    def spy(module_id, task_id=None):
        # 后台任务开始执行之前，任务就已经在列表里了（前端请求一返回就拉列表）
        seen_at_response.append([t["status"] for t in task_center.tasks() if t["kind"] == "module_index"])
        original(module_id, task_id)

    monkeypatch.setattr(modules_api, "_rag_index_bg", spy)
    r = client.post(f"/api/modules/{mid}/rag/rebuild")
    assert r.status_code == 200
    assert seen_at_response == [["running"]]
    [task] = [t for t in task_center.tasks() if t["kind"] == "module_index"]
    assert task["status"] == "done" and task["title"] == "模组《雾港失灯事件》原文索引"
