"""规则书导入队列：上传立即返回，后台逐本处理并汇报进度。

为什么是「解析进子进程、嵌入串行排队」而不是多本并行：
- **解析**是纯 Python（pypdf 逐页取字号），400 页的守秘人规则书约 10 秒、全程持有 GIL。
  放在线程里会把整个服务的事件循环卡住 10 秒，所以进子进程（spawn，只 import 解析模块）。
- **嵌入**才是大头（large 模型约 5 分钟），但 onnxruntime 本身已用满所有核、且释放 GIL；
  多本同时嵌入不会更快，只会抢同一批核，各开进程还要各加载一份模型（large 每份约 2.6GB）。
  所以排队逐本做，按批汇报「已嵌入 / 总数」。

任务状态只在内存里；规则书记录本身（status=indexing）和本机保存的 PDF 原件落在库里，
服务重启后 ``resume_on_startup`` 按原件把没做完的重新排上。
"""
from __future__ import annotations

import logging
import multiprocessing
import threading
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass

from app import database
from app.models.rulebook import Rulebook
from app.services import rulebook_parser, rulebook_service, task_center

logger = logging.getLogger(__name__)


@dataclass
class Job:
    rulebook_id: str
    #: 升级重建（已有可用索引）：失败时保留旧索引；首次导入失败则标 failed
    keep_on_failure: bool = False
    stage: str = "queued"  # queued / parsing / embedding / saving
    done: int = 0
    total: int = 0
    cancelled: bool = False
    #: 「解析进度」抽屉里的任务（task_center）
    task_id: str | None = None


#: 各阶段在「解析进度」抽屉里的说法
_STAGE_LABEL = {"queued": "排队中", "parsing": "解析 PDF", "embedding": "建索引", "saving": "写入索引"}
_LINK = {"to": "/rules?tab=books", "label": "查看规则书"}

_lock = threading.Lock()
_queue: deque[Job] = deque()
_jobs: dict[str, Job] = {}
_worker: threading.Thread | None = None
_pool: ProcessPoolExecutor | None = None


def _parse(pdf_bytes: bytes):
    """在子进程里解析 PDF（不占主进程的 GIL）。测试替换这里即可不起进程。"""
    global _pool
    if _pool is None:
        _pool = ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"))
    return _pool.submit(rulebook_parser.parse_pdf, pdf_bytes).result()


def enqueue(rulebook_id: str, *, keep_on_failure: bool = False, title: str = "") -> None:
    """排上一本（已在队列或处理中则忽略）。"""
    global _worker
    with _lock:
        if rulebook_id in _jobs:
            return
        action = "重建索引" if keep_on_failure else "导入"
        job = Job(rulebook_id, keep_on_failure, task_id=task_center.start(
            "rulebook", f"规则书《{title}》{action}" if title else f"规则书{action}", link=_LINK,
        ))
        _jobs[rulebook_id] = job
        _queue.append(job)
        if _worker is None:
            _worker = threading.Thread(target=_run, name="rulebook-ingest", daemon=True)
            _worker.start()


def cancel(rulebook_id: str) -> None:
    """删除规则书时调用：排队的直接移除，处理中的在下一批嵌入前中止。"""
    with _lock:
        job = _jobs.get(rulebook_id)
        if job is None:
            return
        job.cancelled = True
        task_center.finish(job.task_id, "cancelled", stage="规则书已删除")
        if job in _queue:
            _queue.remove(job)
            _jobs.pop(rulebook_id, None)


def progress(rulebook_id: str) -> dict | None:
    """``{"stage", "done", "total", "queue_position"}``；不在队列里返回 None。"""
    with _lock:
        job = _jobs.get(rulebook_id)
        if job is None:
            return None
        position = list(_queue).index(job) + 1 if job in _queue else 0
        return {"stage": job.stage, "done": job.done, "total": job.total, "queue_position": position}


def wait_idle(timeout: float | None = None) -> None:
    """等队列清空（测试与关停用）。"""
    t = _worker
    if t is not None:
        t.join(timeout)


def _run() -> None:
    global _worker
    while True:
        with _lock:
            if not _queue:
                _worker = None
                return
            job = _queue.popleft()
        try:
            _process(job)
        except Exception as exc:
            logger.exception("规则书导入任务出错：%s", job.rulebook_id)
            task_center.finish(job.task_id, "failed", detail=str(exc)[:300])
        finally:
            with _lock:
                _jobs.pop(job.rulebook_id, None)


def _process(job: Job) -> None:
    with database.SessionLocal() as db:
        book = db.get(Rulebook, job.rulebook_id)
        source = rulebook_service.source_path(book) if book else None
        if book is None or job.cancelled:
            task_center.finish(job.task_id, "cancelled", stage="规则书已删除")
            return
        if source is None or not source.exists():
            if not job.keep_on_failure:
                book.status, book.error = "failed", "找不到规则书原件，请重新上传"
                db.commit()
            task_center.finish(job.task_id, "failed", detail="找不到规则书原件，请重新上传")
            return
        pdf = source.read_bytes()
        _set_stage(job, "parsing")
        try:
            parsed = _parse(pdf)
        except Exception as exc:  # noqa: BLE001 — 坏 PDF 落状态，不中断队列
            logger.exception("规则书解析失败：%s", book.title)
            if not job.keep_on_failure:
                book.status = "failed"
            book.error = f"解析 PDF 失败：{exc}"[:500]
            db.commit()
            task_center.finish(job.task_id, "failed", detail=book.error)
            return

        def on_progress(stage: str, done: int, total: int) -> bool:
            _set_stage(job, stage, done, total)
            return not job.cancelled

        rulebook_service.ingest_rulebook(
            db, book, pdf, parsed=parsed, on_progress=on_progress,
            keep_on_failure=job.keep_on_failure,
        )
        if job.cancelled:
            return
        if book.status == "ready" and not book.error:
            task_center.finish(job.task_id, "done", stage=f"完成：{book.page_count} 页 / {book.chunk_count} 片段")
        else:
            task_center.finish(job.task_id, "failed", detail=book.error or "导入失败")


def _set_stage(job: Job, stage: str, done: int = 0, total: int = 0) -> None:
    job.stage, job.done, job.total = stage, done, total
    task_center.update(job.task_id, stage=_STAGE_LABEL.get(stage, stage), done=done, total=total)


def resume_on_startup() -> None:
    """启动时：中断在半路的导入按原件重新排上；切块逻辑落后且有原件的书排上升级重建。"""
    try:
        with database.SessionLocal() as db:
            for book in db.query(Rulebook).filter(Rulebook.status == "indexing").all():
                if rulebook_service.source_path(book) is not None:
                    enqueue(book.id, title=book.title)
                else:
                    book.status, book.error = "failed", "导入被中断且没有保存原件，请重新上传"
            db.commit()
            for book in rulebook_service.outdated_rulebooks(db):
                logger.info("规则书切块逻辑已升级，排队按原件重建：%s", book.title)
                enqueue(book.id, keep_on_failure=True, title=book.title)
    except Exception:
        logger.exception("恢复规则书导入队列时出错（忽略）")


def shutdown() -> None:
    global _pool
    if _pool is not None:
        _pool.shutdown(wait=False, cancel_futures=True)
        _pool = None
