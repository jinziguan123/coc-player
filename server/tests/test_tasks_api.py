"""「解析进度」抽屉的后台任务汇总：任务中心登记、模组上传任务映射、移除规则、各类任务接入。"""
import time

import pytest
from fastapi.testclient import TestClient

from app.api import modules as modules_api
from app.main import app
from app.services import task_center


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(modules_api, "_upload_jobs", {})
    return TestClient(app, client=("127.0.0.1", 5000))


def test_任务中心_进度百分比与结束状态():
    tid = task_center.start("rulebook", "规则书《甲》导入")
    task_center.update(tid, stage="建索引", done=50, total=200)
    view = next(t for t in task_center.tasks() if t["id"] == tid)
    assert (view["status"], view["stage"], view["percent"]) == ("running", "建索引", 25)

    task_center.finish(tid, "done", stage="完成")
    task_center.update(tid, stage="不该再变")          # 结束后的更新忽略
    view = next(t for t in task_center.tasks() if t["id"] == tid)
    assert (view["status"], view["stage"], view["percent"]) == ("done", "完成", 100)


def test_没有总量时百分比为空_前端显示不定进度():
    tid = task_center.start("module_index", "模组原文索引", stage="解析 PDF")
    assert next(t for t in task_center.tasks() if t["id"] == tid)["percent"] is None


def test_进行中的任务不能移除_结束的可以():
    tid = task_center.start("embed_download", "下载")
    assert task_center.dismiss(tid) is False
    task_center.finish(tid, "failed", detail="网络错误")
    assert task_center.dismiss(tid) is True
    assert task_center.tasks() == []


def test_已结束的只保留最近若干条(monkeypatch):
    monkeypatch.setattr(task_center, "MAX_FINISHED", 3)
    running = task_center.start("rulebook", "进行中")
    for i in range(5):
        task_center.finish(task_center.start("rulebook", f"已完成{i}"), "done")
    titles = [t["title"] for t in task_center.tasks()]
    assert "进行中" in titles and len(titles) == 4
    assert running in [t["id"] for t in task_center.tasks()]


def test_接口汇总模组上传与其他任务_新的在前(client):
    modules_api._upload_jobs["j1"] = {
        "id": "j1", "status": "done", "stage": "完成", "percent": 100, "detail": "",
        "result": {"id": "m1", "title": "陵墓"}, "files": ["陵墓.pdf"], "created_at": time.time() - 10,
    }
    task_center.start("rulebook", "规则书《守秘人规则书》导入", link={"to": "/rules?tab=books", "label": "查看规则书"})

    items = client.get("/api/tasks").json()
    assert [t["kind"] for t in items] == ["rulebook", "module"]
    module = items[1]
    assert module["id"] == "module:j1" and module["title"] == "陵墓"
    assert module["link"] == {"to": "/modules/m1", "label": "查看模组"}
    assert items[0]["status"] == "running" and items[0]["link"]["to"] == "/rules?tab=books"


def test_接口移除_模组任务与任务中心任务(client):
    modules_api._upload_jobs["j1"] = {
        "id": "j1", "status": "running", "stage": "解析", "percent": 30, "detail": "",
        "result": None, "files": ["a.pdf"], "created_at": time.time(),
    }
    assert client.delete("/api/tasks/module:j1").status_code == 409      # 进行中
    modules_api._upload_jobs["j1"]["status"] = "failed"
    assert client.delete("/api/tasks/module:j1").status_code == 200
    assert "j1" not in modules_api._upload_jobs

    tid = task_center.start("rulebook", "x")
    assert client.delete(f"/api/tasks/{tid}").status_code == 404           # 进行中
    task_center.finish(tid, "done")
    assert client.delete(f"/api/tasks/{tid}").status_code == 200


def test_接口仅限本机():
    remote = TestClient(app, client=("8.8.8.8", 1234))
    assert remote.get("/api/tasks").status_code == 403
