"""规则书导入队列：逐本处理、进度汇报、删除即取消、坏 PDF 不阻塞队列、重启后恢复。

不起子进程（conftest 把解析换成本进程调用 rulebook_service.parse_pdf）、不加载真模型。
"""
import threading

import numpy as np
import pytest

from app import database
from app.models.rulebook import RuleChunk, Rulebook
from app.services import rulebook_jobs, rulebook_service
from app.services.rulebook_parser import ParsedSection


class GatedEmbedder:
    """确定性假嵌入器；``gate`` 未放行时卡在第一次嵌入里（模拟一本书正在嵌入）。"""

    model_name, dim = "fake-jobs", 8

    def __init__(self):
        self.gate = threading.Event()
        self.entered = threading.Event()

    def embed_passages(self, texts):
        self.entered.set()
        assert self.gate.wait(10)
        return [np.ones(self.dim).tolist() for _ in texts]

    def embed_query(self, text):
        return np.ones(self.dim).tolist()


@pytest.fixture
def emb(monkeypatch):
    e = GatedEmbedder()
    monkeypatch.setattr(rulebook_service, "get_embedder", lambda: e)
    # 70 个子块：嵌入分两批（每批 64），能看到中途进度
    secs = [ParsedSection(0, f"词条{i}", [f"词条{i}"], 1, 1, f"第{i}条规则的正文。", [(0, 1)]) for i in range(70)]
    monkeypatch.setattr(rulebook_service, "parse_pdf", lambda pdf: (1, secs) if pdf != b"bad" else (_ for _ in ()).throw(ValueError("坏 PDF")))
    return e


def _upload(title, pdf=b"%PDF"):
    with database.SessionLocal() as db:
        book = Rulebook(title=title, rule_system="coc", status="indexing")
        db.add(book)
        db.flush()
        rulebook_service.save_source(book, pdf)
        db.commit()
        rulebook_jobs.enqueue(book.id, title=title)
        return book.id


def _book(book_id):
    with database.SessionLocal() as db:
        return db.get(Rulebook, book_id)


def test_逐本处理并汇报进度(emb):
    first, second = _upload("一"), _upload("二")
    assert emb.entered.wait(5)
    p1, p2 = rulebook_jobs.progress(first), rulebook_jobs.progress(second)
    assert p1["stage"] == "embedding" and p1 == {**p1, "done": 0, "total": 70}
    assert p2 == {"stage": "queued", "done": 0, "total": 0, "queue_position": 1}  # 第二本排队等着

    emb.gate.set()
    rulebook_jobs.wait_idle(10)
    assert rulebook_jobs.progress(first) is None and rulebook_jobs.progress(second) is None
    assert _book(first).status == _book(second).status == "ready"
    assert _book(first).chunk_count == 70


def test_删除排队中的书_不再处理(emb):
    first, second = _upload("一"), _upload("二")
    assert emb.entered.wait(5)
    rulebook_jobs.cancel(second)
    with database.SessionLocal() as db:
        rulebook_service.delete_rulebook(db, second)
    assert rulebook_jobs.progress(second) is None
    emb.gate.set()
    rulebook_jobs.wait_idle(10)
    assert _book(first).status == "ready"
    with database.SessionLocal() as db:
        assert db.query(RuleChunk).filter(RuleChunk.rulebook_id == second).count() == 0


def test_删除导入中的书_中止且不写库(emb):
    book_id = _upload("一")
    assert emb.entered.wait(5)
    rulebook_jobs.cancel(book_id)
    with database.SessionLocal() as db:
        rulebook_service.delete_rulebook(db, book_id)
    emb.gate.set()
    rulebook_jobs.wait_idle(10)
    with database.SessionLocal() as db:
        assert db.get(Rulebook, book_id) is None
        assert db.query(RuleChunk).count() == 0


def test_坏PDF标失败_不阻塞后面的书(emb):
    emb.gate.set()
    bad, good = _upload("坏", b"bad"), _upload("好")
    rulebook_jobs.wait_idle(10)
    assert _book(bad).status == "failed" and "坏 PDF" in _book(bad).error
    assert _book(good).status == "ready"


def test_重启后中断的导入按原件重排_没原件的标失败(emb):
    emb.gate.set()
    with database.SessionLocal() as db:
        with_src = Rulebook(title="有原件", rule_system="coc", status="indexing")
        no_src = Rulebook(title="无原件", rule_system="coc", status="indexing")
        db.add_all([with_src, no_src])
        db.flush()
        rulebook_service.save_source(with_src, b"%PDF")
        db.commit()
        ids = with_src.id, no_src.id

    rulebook_jobs.resume_on_startup()
    rulebook_jobs.wait_idle(10)
    assert _book(ids[0]).status == "ready"
    assert _book(ids[1]).status == "failed" and "重新上传" in _book(ids[1]).error


def test_重启时切块逻辑落后且有原件的书排队重建_失败保留旧索引(emb, monkeypatch):
    emb.gate.set()
    book_id = _upload("旧书")
    rulebook_jobs.wait_idle(10)
    with database.SessionLocal() as db:
        db.get(Rulebook, book_id).chunker_version = 0
        db.commit()

    rulebook_jobs.resume_on_startup()
    rulebook_jobs.wait_idle(10)
    assert _book(book_id).chunker_version == rulebook_service.CHUNKER_VERSION

    with database.SessionLocal() as db:
        db.get(Rulebook, book_id).chunker_version = 0
        db.commit()
    monkeypatch.setattr(rulebook_service, "parse_pdf", lambda pdf: (_ for _ in ()).throw(ValueError("坏")))
    rulebook_jobs.resume_on_startup()
    rulebook_jobs.wait_idle(10)
    book = _book(book_id)
    assert book.status == "ready" and book.chunk_count == 70   # 旧索引仍可用
    assert "坏" in book.error


def _tasks(kind="rulebook"):
    from app.services import task_center

    return [t for t in task_center.tasks() if t["kind"] == kind]


def test_导入任务登记到解析进度(emb):
    book_id = _upload("守秘人规则书")
    assert emb.entered.wait(5)
    [task] = _tasks()
    assert task["status"] == "running" and task["stage"] == "建索引" and task["percent"] == 0
    assert task["title"] == "规则书《守秘人规则书》导入"
    emb.gate.set()
    rulebook_jobs.wait_idle(10)
    [task] = _tasks()
    assert task["status"] == "done" and task["stage"].startswith("完成") and task["link"]["to"] == "/rules?tab=books"
    assert _book(book_id).status == "ready"


def test_取消与失败也落到解析进度(emb):
    emb.gate.set()
    bad = _upload("坏", b"bad")
    rulebook_jobs.wait_idle(10)
    assert [t["status"] for t in _tasks()] == ["failed"]
    assert "坏 PDF" in _tasks()[0]["detail"]
    assert _book(bad).status == "failed"
