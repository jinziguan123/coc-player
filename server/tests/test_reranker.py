"""精排：注册参数、开关与是否已下载、KP 主动查阅模组原文时重排、失败回落、删除即关闭、接口。

不下载、不加载真实模型：精排器用假实现。
"""
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import database
from app.ai import embedding_store, reranker
from app.ai.embedding import RERANKERS
from app.models.module import Module, ModuleChunk
from app.services import module_rag_service, task_center

SPEC = next(iter(RERANKERS.values()))


class KeywordReranker:
    """假精排器：文档里含「钥匙」的得分高。"""

    def __init__(self):
        self.calls = []

    def scores(self, query, documents):
        self.calls.append((query, list(documents)))
        return [10.0 if "钥匙" in d else 0.0 for d in documents]


class FixedEmbedder:
    model_name, dim = "fixed", 2

    def embed_query(self, text):
        return [1.0, 0.0]


@pytest.fixture
def module_with_chunks():
    db = database.SessionLocal()
    m = Module(title="雨鲸", rule_system="coc", raw_content="x", scenes=[], npcs=[], clues=[], rag_status="ready")
    db.add(m)
    db.flush()
    # 向量排序：寝室 > 书房 > 杂物间（答案在最后）
    for i, (text, vec) in enumerate([
        ("寝室 3 是护工的房间。", [1.0, 0.0]),
        ("书房里堆满了书。", [0.9, 0.1]),
        ("杂物间平时上锁，钥匙由护工约翰保管。", [0.8, 0.2]),
    ]):
        db.add(ModuleChunk(module_id=m.id, ordinal=i, text=text,
                           embedding=np.asarray(vec, dtype=np.float32).tobytes()))
    db.commit()
    return db, m


def test_注册参数():
    kw = reranker.custom_model_kwargs(SPEC)
    assert kw["model"] == f"coc-player/{SPEC.id}"
    assert kw["model_file"] == "onnx/model_int8.onnx"
    assert SPEC.kind == "reranker" and SPEC.max_length == 1024


def test_未开启或未下载时不精排(monkeypatch):
    assert reranker.get_reranker() is None                       # 默认关闭
    with pytest.raises(ValueError, match="尚未下载"):
        reranker.set_enabled(True)
    monkeypatch.setattr(embedding_store, "is_installed", lambda spec: True)
    monkeypatch.setattr(embedding_store, "installed_dir", lambda spec: "/x")
    reranker.set_enabled(True)
    assert isinstance(reranker.get_reranker(), reranker.Reranker)
    # 开关存在自己的设置文件里，与检索模型的设置互不相干
    assert reranker.SETTINGS_FILE.exists() and not embedding_store.SETTINGS_FILE.exists()
    reranker.set_enabled(False)
    assert reranker.get_reranker() is None


def test_主动查阅时精排把答案排到前面(monkeypatch, module_with_chunks):
    db, m = module_with_chunks
    fake = KeywordReranker()
    monkeypatch.setattr(reranker, "get_reranker", lambda: fake)

    plain = module_rag_service.retrieve(db, m.id, "杂物间钥匙", k=1, embedder=FixedEmbedder())
    assert plain[0]["text"].startswith("寝室")                  # 不精排：向量第一
    assert fake.calls == []                                      # 被动检索不调精排

    hits = module_rag_service.retrieve(db, m.id, "杂物间钥匙", k=1, embedder=FixedEmbedder(), rerank=True)
    assert hits[0]["text"].startswith("杂物间") and hits[0]["rerank_score"] == 10.0
    assert len(fake.calls[0][1]) == 3                            # 候选是向量取回的前 N 个


def test_精排出错回落向量排序(monkeypatch, module_with_chunks):
    db, m = module_with_chunks

    class Boom:
        def scores(self, q, d):
            raise RuntimeError("onnx 崩了")

    monkeypatch.setattr(reranker, "get_reranker", lambda: Boom())
    hits = module_rag_service.retrieve(db, m.id, "杂物间钥匙", k=1, embedder=FixedEmbedder(), rerank=True)
    assert hits[0]["text"].startswith("寝室")


def test_删除精排模型时开关一并关闭(monkeypatch):
    monkeypatch.setattr(embedding_store, "is_installed", lambda spec: True)
    reranker.set_enabled(True)
    embedding_store.delete_model(SPEC.id)
    assert reranker.is_enabled() is False


def test_下载任务标题区分精排模型(monkeypatch):
    j = embedding_store._new_job(SPEC)
    [task] = [t for t in task_center.tasks() if t["id"] == j.task_id]
    assert task["title"] == f"下载精排模型 {SPEC.label}"


def test_接口_精排与检索模型分开管理():
    from app.main import app

    local = TestClient(app, client=("127.0.0.1", 5000))
    body = local.get("/api/settings/reranker").json()
    assert [m["id"] for m in body["models"]] == [SPEC.id]
    assert body["enabled"] is False and body["models"][0]["installed"] is False
    r = local.put("/api/settings/reranker", json={"enabled": True})
    assert r.status_code == 400 and "尚未下载" in r.json()["detail"]

    # 检索模型接口里看不到精排模型，也不能用它下载/删除精排模型，反之亦然
    emb = local.get("/api/settings/embedding").json()
    assert SPEC.id not in [m["id"] for m in emb["models"]] and "rerankers" not in emb
    assert local.post(f"/api/settings/embedding/models/{SPEC.id}/download").status_code == 404
    assert local.post("/api/settings/reranker/models/bge-small-zh-v1.5/download").status_code == 404


def test_接口_模型下载源单独设置():
    from app.main import app

    local = TestClient(app, client=("127.0.0.1", 5000))
    assert local.get("/api/settings/model-source").json() == {"source": "modelscope", "hf_endpoint": ""}
    r = local.put("/api/settings/model-source", json={"source": "huggingface", "hf_endpoint": "https://hf-mirror.example.com/"})
    assert r.json() == {"source": "huggingface", "hf_endpoint": "https://hf-mirror.example.com"}
    assert local.put("/api/settings/model-source", json={"source": "ftp", "hf_endpoint": ""}).status_code == 400


def test_旧版存在检索模型设置里的精排开关会迁移过来(monkeypatch):
    import json

    embedding_store.SETTINGS_FILE.write_text(json.dumps({"active": "bge-small-zh-v1.5", "reranker_enabled": True}))
    assert reranker.is_enabled() is True
    # 立刻写进自己的文件：检索模型设置下次保存会丢掉旧键
    assert json.loads(reranker.SETTINGS_FILE.read_text()) == {"enabled": True}
    embedding_store.set_source("modelscope")
    monkeypatch.setattr(reranker, "_enabled", None)   # 模拟重启
    assert reranker.is_enabled() is True
