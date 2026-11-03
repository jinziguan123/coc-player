"""嵌入模型：自定义模型注册、索引记录模型、热切换重建、下载（失败/取消/续传/镜像）。

全程用假嵌入器与 httpx.MockTransport，不下载、不加载真实模型。
"""
from __future__ import annotations

import hashlib
import json
import threading

import httpx
import numpy as np
import pytest

from app import database
from app.ai import embedding, embedding_store
from app.ai.embedding import CATALOG, EmbedModelSpec, FastEmbedEmbedder, ModelFile
from app.models.character import Character
from app.models.event_log import EventLog
from app.models.module import Module, ModuleChunk
from app.models.rulebook import RuleChunk, Rulebook
from app.models.session import GameSession
from app.services import embedding_switch, event_recall, module_rag_service, rulebook_service

SMALL = CATALOG["bge-small-zh-v1.5"]
LARGE = CATALOG["bge-large-zh-v1.5-int8"]
M3 = CATALOG["bge-m3-int8"]


class FakeEmbedder:
    """确定性假嵌入器：维度与模型名可配，记录查询调用；``gate`` 可让建向量卡住（模拟重建进行中）。"""

    def __init__(self, name: str, dim: int):
        self.model_name, self.dim = name, dim
        self.queries: list[str] = []
        self.gate: threading.Event | None = None
        self.entered = threading.Event()

    def _vec(self, text: str) -> list[float]:
        v = np.zeros(self.dim)
        for ch in text:
            v[int(hashlib.md5(ch.encode()).hexdigest()[:8], 16) % self.dim] += 1.0
        n = np.linalg.norm(v)
        return (v / n if n else v).tolist()

    def embed_passages(self, texts):
        self.entered.set()
        if self.gate is not None:
            assert self.gate.wait(10)
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        self.queries.append(text)
        return self._vec(text)


@pytest.fixture
def fakes(monkeypatch):
    """每个目录内模型一个假嵌入器，并视为都已下载。"""
    fs = {SMALL.name: FakeEmbedder(SMALL.name, 8), LARGE.name: FakeEmbedder(LARGE.name, 12),
          M3.name: FakeEmbedder(M3.name, 12)}
    monkeypatch.setattr(embedding, "_load", lambda name: fs.get(name))
    monkeypatch.setattr(embedding_store, "is_installed", lambda spec: True)
    return fs


# ── 自定义模型注册 ──────────────────────────────────────────────────────


@pytest.mark.parametrize("spec", [LARGE, M3])
def test_自定义模型注册参数(spec):
    from fastembed.common.model_description import PoolingType

    kw = embedding.custom_model_kwargs(spec)
    assert kw["pooling"] == PoolingType.CLS
    assert kw["normalization"] is True
    assert kw["dim"] == 1024
    assert kw["model_file"] == "onnx/model_int8.onnx"
    assert kw["model_file"] in {f.path for f in spec.files}
    # 用自己的命名空间，不与 fastembed 内置模型重名
    assert kw["model"].startswith("coc-player/")


def test_large_查询加指令前缀_passage不加_小批量建向量(tmp_path):
    calls = {}

    class Stub:
        def query_embed(self, texts):
            calls["query"] = texts
            yield np.zeros(4)

        def passage_embed(self, texts, batch_size):
            calls["passage"], calls["batch"] = texts, batch_size
            return [np.zeros(4) for _ in texts]

    emb = FastEmbedEmbedder(LARGE, tmp_path)
    emb._model = Stub()
    emb.embed_query("孤注一掷")
    emb.embed_passages(["正文"])
    assert calls["query"] == [embedding.BGE_ZH_QUERY_PREFIX + "孤注一掷"]
    assert calls["passage"] == ["正文"]
    assert calls["batch"] == embedding.PASSAGE_BATCH_SIZE == 16

    small = FastEmbedEmbedder(SMALL, tmp_path)
    small._model = Stub()
    small.embed_query("孤注一掷")
    assert calls["query"] == ["孤注一掷"]  # small 维持存量索引的建法：不加前缀


def test_旧版fastembed缓存视为已下载(tmp_path):
    snap = embedding_store.MODELS_DIR / SMALL.legacy_cache / "snapshots" / "abc"
    snap.mkdir(parents=True)
    for f in SMALL.files:
        (snap / f.path).write_bytes(b"x")
    assert embedding_store.installed_dir(SMALL) == snap
    assert embedding_store.installed_dir(LARGE) is None


# ── 数据准备 ────────────────────────────────────────────────────────────


def _seed(db, model_name: str, emb: FakeEmbedder):
    """一本规则书 + 一个模组原文索引 + 一局事件向量，全由 ``model_name`` 建。"""
    book = Rulebook(title="规则书", rule_system="coc", status="ready", embed_model=model_name)
    module = Module(title="模组", rule_system="coc", raw_content="", scenes=[], npcs=[], clues=[],
                    rag_status="ready", rag_embed_model=model_name)
    player = Character(name="亨利", rule_system="coc")
    db.add_all([book, module, player])
    db.flush()
    texts = ["孤注一掷可以重骰一次失败的检定", "理智损失五点以上进入临时性疯狂"]
    for i, t in enumerate(texts):
        vec = np.asarray(emb._vec(t), dtype=np.float32).tobytes()
        db.add(RuleChunk(rulebook_id=book.id, rule_system="coc", ordinal=i, text=t, embedding=vec))
        db.add(ModuleChunk(module_id=module.id, ordinal=i, text=t, embedding=vec))
    gs = GameSession(module_id=module.id, player_character_id=player.id, status="active")
    db.add(gs)
    db.flush()
    for i, t in enumerate(["我们在地窖里找到了一把铜钥匙", "山田的腿被怪物咬伤了"]):
        db.add(EventLog(session_id=gs.id, sequence_num=i + 1, event_type="narration", content=t,
                        embedding=np.asarray(emb._vec(t), dtype=np.float32).tobytes(),
                        embed_model=model_name))
    db.commit()
    return book, module, gs


# ── 索引记录模型 ────────────────────────────────────────────────────────


def test_建索引记录模型名(fakes):
    db = database.SessionLocal()
    module = Module(title="模组", rule_system="coc", raw_content="第一章\n" + "大堂里霉味扑面。" * 40,
                    scenes=[], npcs=[], clues=[])
    player = Character(name="亨利", rule_system="coc")
    db.add_all([module, player])
    db.flush()
    gs = GameSession(module_id=module.id, player_character_id=player.id, status="active")
    db.add(gs)
    db.flush()
    db.add(EventLog(session_id=gs.id, sequence_num=1, event_type="narration", content="长廊尽头的烛火忽明忽暗。"))
    db.commit()

    module_rag_service.ingest_module(db, module)  # 不传 embedder：走写入模型（默认 small）
    assert module.rag_status == "ready"
    assert module.rag_embed_model == SMALL.name
    assert event_recall.index_pending(db, gs.id) == 1
    assert db.query(EventLog).one().embed_model == SMALL.name


def test_模型不可用的索引不检索(fakes, monkeypatch):
    db = database.SessionLocal()
    book, module, gs = _seed(db, "unknown/old-model", fakes[SMALL.name])
    # 不认识的模型：宁可查不到，也不拿当前模型去查旧向量
    assert rulebook_service.retrieve(db, "孤注一掷", "coc") == []
    assert module_rag_service.retrieve(db, module.id, "孤注一掷") == []
    assert event_recall.recall(db, gs.id, "钥匙") == []
    assert fakes[SMALL.name].queries == []
    # 后台重建会把它们识别为「与当前模型不一致」
    assert embedding_switch.stale_count(db, SMALL.name) == 6


def test_规则书混用两个模型时各用各的模型查询(fakes):
    db = database.SessionLocal()
    _seed(db, SMALL.name, fakes[SMALL.name])
    other = Rulebook(title="新规则书", rule_system="coc", status="ready", embed_model=LARGE.name)
    db.add(other)
    db.flush()
    t = "孤注一掷的规则补充说明"
    db.add(RuleChunk(rulebook_id=other.id, rule_system="coc", ordinal=0, text=t,
                     embedding=np.asarray(fakes[LARGE.name]._vec(t), dtype=np.float32).tobytes()))
    db.commit()

    hits = rulebook_service.retrieve(db, "孤注一掷", "coc", k=3)
    assert {h["rulebook_id"] for h in hits} == {other.id, db.query(Rulebook).filter_by(title="规则书").one().id}
    assert fakes[SMALL.name].queries == ["孤注一掷"]
    assert fakes[LARGE.name].queries == ["孤注一掷"]


# ── 热切换 ──────────────────────────────────────────────────────────────


def _all_models(db):
    return (
        {b.embed_model for b in db.query(Rulebook)},
        {m.rag_embed_model for m in db.query(Module)},
        {e.embed_model for e in db.query(EventLog)},
    )


def test_切换后后台重建所有索引并一次性切换(fakes):
    db = database.SessionLocal()
    _seed(db, SMALL.name, fakes[SMALL.name])

    embedding_switch.request_switch(LARGE.id)
    assert embedding_store.load().target == LARGE.id
    embedding_switch.wait(10)

    db.expire_all()
    assert _all_models(db) == ({LARGE.name}, {LARGE.name}, {LARGE.name})
    assert all(len(c.embedding) == 12 * 4 for c in db.query(RuleChunk))  # 维度换成新模型的
    s = embedding_store.load()
    assert (s.active, s.target) == (LARGE.id, None)
    st = embedding_switch.state()
    assert not st.running and st.done == st.total == 6 and st.error == ""
    # 旧模型已卸载，新模型在用
    assert set(embedding._embedders) == {LARGE.name}
    assert rulebook_service.retrieve(db, "孤注一掷", "coc")
    assert fakes[LARGE.name].queries


def test_重建期间旧索引仍用旧模型查询(fakes):
    db = database.SessionLocal()
    _, module, gs = _seed(db, SMALL.name, fakes[SMALL.name])
    large = fakes[LARGE.name]
    large.gate = threading.Event()

    embedding_switch.request_switch(LARGE.id)
    assert large.entered.wait(10)  # 重建卡在第一个索引上
    try:
        assert embedding_store.active_model().id == SMALL.id  # 还没切
        assert embedding_switch.state().running
        assert module_rag_service.retrieve(db, module.id, "孤注一掷")
        assert event_recall.recall(db, gs.id, "钥匙")
        assert rulebook_service.retrieve(db, "孤注一掷", "coc")
        assert large.queries == []  # 旧索引一律用建它的旧模型查
        assert len(fakes[SMALL.name].queries) == 3
        # 重建中新建的索引直接用目标模型
        assert embedding.get_embedder().model_name == LARGE.name
    finally:
        large.gate.set()
    embedding_switch.wait(10)
    assert embedding_store.active_model().id == LARGE.id


def test_取消切换会把已换的索引换回去(fakes):
    db = database.SessionLocal()
    _seed(db, SMALL.name, fakes[SMALL.name])
    large = fakes[LARGE.name]
    large.gate = threading.Event()

    embedding_switch.request_switch(LARGE.id)
    assert large.entered.wait(10)
    embedding_switch.request_switch(SMALL.id)  # 传当前模型＝取消
    large.gate.set()
    embedding_switch.wait(10)

    db.expire_all()
    assert _all_models(db) == ({SMALL.name}, {SMALL.name}, {SMALL.name})
    s = embedding_store.load()
    assert (s.active, s.target) == (SMALL.id, None)


def test_重启后接着做未完成的切换(fakes):
    db = database.SessionLocal()
    _seed(db, SMALL.name, fakes[SMALL.name])
    s = embedding_store.load()
    s.target = M3.id
    embedding_store.save(s)
    embedding_store._cache = None  # 模拟重启：从文件读

    embedding_switch.resume_if_needed()
    embedding_switch.wait(10)
    assert embedding_store.active_model().id == M3.id
    db.expire_all()
    assert _all_models(db) == ({M3.name}, {M3.name}, {M3.name})


def test_未下载的模型不能切换():
    with pytest.raises(ValueError, match="尚未下载"):
        embedding_switch.request_switch(LARGE.id)


# ── 下载 ────────────────────────────────────────────────────────────────


def _tiny_spec(monkeypatch, files: dict[str, bytes], max_length=None) -> EmbedModelSpec:
    spec = EmbedModelSpec(
        id="tiny", name="test/tiny", label="tiny", description="", dim=4, repo="Org/tiny",
        files=tuple(ModelFile(p, len(b), hashlib.sha256(b).hexdigest()) for p, b in files.items()),
        model_file="onnx/model.onnx", max_length=max_length,
    )
    monkeypatch.setitem(CATALOG, "tiny", spec)
    return spec


def _serve(monkeypatch, handler):
    seen: list[httpx.Request] = []

    def wrapped(request):
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(
        embedding_store, "_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(wrapped), follow_redirects=True),
    )
    return seen


FILES = {
    "tokenizer_config.json": json.dumps({"model_max_length": 10**30}).encode(),
    "onnx/model.onnx": b"0123456789" * 50,
}


def _ok(files):
    def handler(request):
        name = request.url.path.split("/resolve/")[1].split("/", 1)[1]
        return httpx.Response(200, content=files[name])
    return handler


def _run_download(spec):
    j = embedding_store.start_download(spec.id)
    for _ in range(200):
        if j.state != "downloading":
            break
        threading.Event().wait(0.02)
    return j


def test_下载成功_校验并改写最大长度_默认走ModelScope(monkeypatch):
    spec = _tiny_spec(monkeypatch, FILES, max_length=512)
    seen = _serve(monkeypatch, _ok(FILES))
    j = _run_download(spec)
    assert j.state == "done", j.error
    assert j.done_bytes == j.total_bytes == spec.size_bytes
    d = embedding_store.installed_dir(spec)
    assert d is not None and (d / "onnx/model.onnx").read_bytes() == FILES["onnx/model.onnx"]
    assert json.loads((d / "tokenizer_config.json").read_text())["model_max_length"] == 512
    assert all(str(r.url).startswith("https://www.modelscope.cn/models/Org/tiny/resolve/master/") for r in seen)


def test_下载源设为HuggingFace镜像生效(monkeypatch):
    spec = _tiny_spec(monkeypatch, FILES)
    embedding_store.set_source("huggingface", "https://hf-mirror.example.com/")
    seen = _serve(monkeypatch, _ok(FILES))
    assert _run_download(spec).state == "done"
    assert {str(r.url) for r in seen} == {
        "https://hf-mirror.example.com/Org/tiny/resolve/main/tokenizer_config.json",
        "https://hf-mirror.example.com/Org/tiny/resolve/main/onnx/model.onnx",
    }


def test_HuggingFace未填地址时取HF_ENDPOINT(monkeypatch):
    spec = _tiny_spec(monkeypatch, FILES)
    embedding_store.set_source("huggingface", "")
    monkeypatch.setenv("HF_ENDPOINT", "https://proxy.example.org")
    assert embedding_store.file_url(spec, "onnx/model.onnx") == (
        "https://proxy.example.org/Org/tiny/resolve/main/onnx/model.onnx"
    )
    with pytest.raises(ValueError):
        embedding_store.set_source("huggingface", "ftp://bad")


def test_下载失败落状态且不算已下载(monkeypatch):
    spec = _tiny_spec(monkeypatch, FILES)
    _serve(monkeypatch, lambda r: httpx.Response(500))
    j = _run_download(spec)
    assert j.state == "failed"
    assert "HTTP 500" in j.error
    assert not embedding_store.is_installed(spec)


def test_校验不通过视为失败(monkeypatch):
    spec = _tiny_spec(monkeypatch, FILES)
    bad = dict(FILES, **{"onnx/model.onnx": b"x" * len(FILES["onnx/model.onnx"])})
    _serve(monkeypatch, _ok(bad))
    j = _run_download(spec)
    assert j.state == "failed" and "校验失败" in j.error
    assert not embedding_store.is_installed(spec)


def test_网络中断后续传(monkeypatch):
    spec = _tiny_spec(monkeypatch, FILES)
    body = FILES["onnx/model.onnx"]
    partial = embedding_store.MODELS_DIR / "embed" / ".partial" / "tiny" / "onnx"
    partial.mkdir(parents=True)
    (partial / "model.onnx").write_bytes(body[:123])

    def handler(request):
        if request.url.path.endswith("model.onnx"):
            assert request.headers["Range"] == "bytes=123-"
            return httpx.Response(206, content=body[123:])
        return httpx.Response(200, content=FILES["tokenizer_config.json"])

    _serve(monkeypatch, handler)
    assert _run_download(spec).state == "done"
    assert (embedding_store.installed_dir(spec) / "onnx/model.onnx").read_bytes() == body


def test_取消下载清掉半截文件(monkeypatch):
    spec = _tiny_spec(monkeypatch, FILES)
    holder = {}

    def stream():
        yield FILES["onnx/model.onnx"][:10]
        embedding_store.cancel_download(holder["id"])
        yield FILES["onnx/model.onnx"][10:]

    def handler(request):
        if request.url.path.endswith("model.onnx"):
            return httpx.Response(200, content=stream())
        return httpx.Response(200, content=FILES["tokenizer_config.json"])

    holder["id"] = spec.id
    _serve(monkeypatch, handler)
    j = _run_download(spec)
    assert j.state == "cancelled"
    assert not embedding_store.is_installed(spec)
    assert not (embedding_store.MODELS_DIR / "embed" / ".partial" / "tiny").exists()


def test_使用中的模型不能删除_其他可删(monkeypatch):
    spec = _tiny_spec(monkeypatch, FILES)
    _serve(monkeypatch, _ok(FILES))
    assert _run_download(spec).state == "done"
    with pytest.raises(ValueError, match="使用中"):
        embedding_store.delete_model(SMALL.id)
    embedding_store.delete_model(spec.id)
    assert not embedding_store.is_installed(spec)


# ── 接口 ────────────────────────────────────────────────────────────────


def test_接口列表与仅本机(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    local = TestClient(app, client=("127.0.0.1", 5000))
    r = local.get("/api/settings/embedding")
    assert r.status_code == 200, r.text
    body = r.json()
    assert [m["id"] for m in body["models"]] == list(CATALOG)
    assert body["active"] == SMALL.id and body["target"] is None
    assert body["source"] == "modelscope"
    large = next(m for m in body["models"] if m["id"] == LARGE.id)
    assert large["recommended"] and not large["installed"] and large["size_bytes"] == LARGE.size_bytes
    assert local.post("/api/settings/embedding/switch", json={"model_id": LARGE.id}).status_code == 400

    remote = TestClient(app, client=("8.8.8.8", 1234))
    assert remote.get("/api/settings/embedding").status_code == 403


def test_切换重建规则书时子块仍带词条路径入向量(fakes):
    """新版规则书的子块入向量时拼着词条路径；换模型重建必须用同样的文本，否则检索效果悄悄变差。"""
    from app.models.rulebook import RuleSection

    db = database.SessionLocal()
    small = fakes[SMALL.name]
    book = Rulebook(title="新书", rule_system="coc", status="ready", embed_model=SMALL.name)
    db.add(book)
    db.flush()
    sec = RuleSection(rulebook_id=book.id, ordinal=0, level=1, title="行动点",
                      path="第七章 追逐 › 行动点", page_start=114, page_end=114, text="每一轮都能获得行动点。")
    db.add(sec)
    db.flush()
    db.add(RuleChunk(rulebook_id=book.id, rule_system="coc", ordinal=0, page=114, text=sec.text,
                     section_id=sec.id, embedding=np.asarray(small._vec(sec.text), dtype=np.float32).tobytes()))
    db.commit()
    seen: list[str] = []
    large = fakes[LARGE.name]
    original = large.embed_passages
    large.embed_passages = lambda texts: (seen.extend(texts), original(texts))[1]

    embedding_switch.request_switch(LARGE.id)
    embedding_switch.wait(10)
    assert seen == ["第七章 追逐 › 行动点\n每一轮都能获得行动点。"]


def _center(kind):
    from app.services import task_center

    return [t for t in task_center.tasks() if t["kind"] == kind]


def test_下载任务登记到解析进度(monkeypatch):
    spec = _tiny_spec(monkeypatch, FILES)
    _serve(monkeypatch, _ok(FILES))
    assert _run_download(spec).state == "done"
    [task] = _center("embed_download")
    assert task["status"] == "done" and task["title"] == "下载检索模型 tiny"
    assert task["link"] == {"to": "/settings?tab=rag", "label": "查看检索模型"}

    spec2 = _tiny_spec(monkeypatch, FILES)
    monkeypatch.setitem(CATALOG, "tiny", spec2)
    embedding_store.delete_model("tiny")
    _serve(monkeypatch, lambda r: httpx.Response(500))
    assert _run_download(spec2).state == "failed"
    assert [t["status"] for t in _center("embed_download")] == ["done", "failed"]
    assert "HTTP 500" in _center("embed_download")[1]["detail"]


def test_切换重建登记到解析进度(fakes):
    db = database.SessionLocal()
    _seed(db, SMALL.name, fakes[SMALL.name])
    embedding_switch.request_switch(LARGE.id)
    embedding_switch.wait(10)
    [task] = _center("embed_rebuild")
    assert task["status"] == "done" and task["title"] == f"切换检索模型到 {LARGE.label}"
    assert task["stage"] == f"已切换到 {LARGE.label}"


def test_模组原文索引登记到解析进度(fakes, monkeypatch):
    from app.api import modules as modules_api

    # modules 若在别的用例中途才首次导入，手里的 SessionLocal 是那个用例的临时库：这里显式指向本用例的
    monkeypatch.setattr(modules_api, "SessionLocal", database.SessionLocal)
    db = database.SessionLocal()
    module = Module(title="雨鲸", rule_system="coc", raw_content="第一章\n" + "大堂里霉味扑面。" * 40,
                    scenes=[], npcs=[], clues=[], rag_status="indexing")
    db.add(module)
    db.commit()
    modules_api._rag_index_bg(module.id)
    [task] = _center("module_index")
    assert task["title"] == "模组《雨鲸》原文索引" and task["status"] == "done"
    assert task["link"]["to"] == f"/modules/{module.id}"
