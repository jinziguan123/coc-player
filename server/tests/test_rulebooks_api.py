"""规则书 API 的 HTTP 层回归（上传→后台入库→列表→检索→删除）。

不触达真实嵌入模型：把 rulebook_service 的 get_embedder/parse_pdf 打桩，
并把 app.database.SessionLocal 指向测试库（后台任务自开会话）。TestClient 会在
上传只排进导入队列，用例用 rulebook_jobs.wait_idle 等队列跑完再断言 ready。
"""

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import database
from app.ai.embedding import Embedder
from app.database import get_db
from app.main import app
from app.models import Base, RuleChunk, Rulebook  # noqa: F401 注册表
from app.services import rulebook_jobs, rulebook_service
from app.services.rulebook_parser import ParsedSection


class FakeEmbedder(Embedder):
    model_name = "fake-api"
    dim = 64

    def _vec(self, t):
        v = np.zeros(self.dim, dtype=np.float32)
        for ch in t:
            v[ord(ch) % self.dim] += 1.0
        return v.tolist()

    def embed_passages(self, texts):
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)


CANNED = (2, [
    ParsedSection(0, "技能检定", ["技能检定"], 1, 1, "投掷 d100，结果小于等于技能值即为普通成功，难度由守秘人选定。", [(0, 1)]),
    ParsedSection(0, "孤注一掷", ["孤注一掷"], 2, 2, "技能检定失败后可加倍投入重新掷骰一次，若再次失败则后果显著加重。", [(0, 2)]),
])


@pytest.fixture
def client(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'api.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    # 后台入库任务自开 SessionLocal —— 指向测试库，并打桩嵌入/抽取
    monkeypatch.setattr(database, "SessionLocal", TestingSession)
    monkeypatch.setattr(rulebook_service, "get_embedder", lambda: FakeEmbedder())
    # 检索按规则书记录的模型名取嵌入器
    monkeypatch.setattr(
        rulebook_service, "embedder_for",
        lambda name: FakeEmbedder() if name == FakeEmbedder.model_name else None,
    )
    monkeypatch.setattr(rulebook_service, "parse_pdf", lambda b: CANNED)

    yield TestClient(app)
    app.dependency_overrides.clear()


def test_upload_then_indexed_and_searchable(client):
    resp = client.post(
        "/api/rulebooks/upload",
        files={"file": ("coc.pdf", b"%PDF-1.4 fake", "application/pdf")},
        params={"title": "守秘人规则书", "rule_system": "coc"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["title"] == "守秘人规则书"
    # 上传只排队，立即返回 → 仍是 indexing
    assert body["status"] == "indexing"
    assert body["progress"]["stage"] in ("queued", "parsing", "embedding", "saving")

    rulebook_jobs.wait_idle(10)
    lst = client.get("/api/rulebooks").json()
    assert len(lst) == 1
    assert lst[0]["status"] == "ready"
    assert lst[0]["chunk_count"] == 2
    assert lst[0]["embed_model"] == "fake-api"
    # 原件已存进数据目录，索引是新版切块
    assert lst[0]["has_source"] is True and lst[0]["index_outdated"] is False
    assert lst[0]["progress"] is None
    assert (rulebook_service.RULEBOOKS_DIR / f"{lst[0]['id']}.pdf").read_bytes() == b"%PDF-1.4 fake"

    # 检索命中孤注一掷那块
    hits = client.get(
        "/api/rulebooks/search", params={"q": "孤注一掷 重掷", "rule_system": "coc"}
    ).json()
    assert hits["hits"], "应有命中"
    assert "孤注一掷" in hits["hits"][0]["text"]
    assert hits["hits"][0]["page"] == 2


def test_upload_rejects_non_pdf(client):
    resp = client.post(
        "/api/rulebooks/upload",
        files={"file": ("rules.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 422


def test_delete_rulebook(client):
    client.post(
        "/api/rulebooks/upload",
        files={"file": ("coc.pdf", b"%PDF", "application/pdf")},
    )
    rulebook_jobs.wait_idle(10)
    book_id = client.get("/api/rulebooks").json()[0]["id"]

    assert client.delete(f"/api/rulebooks/{book_id}").status_code == 200
    assert client.get("/api/rulebooks").json() == []
    assert client.delete(f"/api/rulebooks/{book_id}").status_code == 404


def test_search_empty_when_no_rulebook(client):
    hits = client.get(
        "/api/rulebooks/search", params={"q": "任何", "rule_system": "coc"}
    ).json()
    assert hits == {"query": "任何", "hits": []}
