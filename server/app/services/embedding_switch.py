"""嵌入模型热切换：后台把所有向量索引重建到目标模型，完成后一次性切换。

流程：``request_switch(id)`` 记下目标模型 → 后台线程逐个找「模型与目标不一致」的索引
（规则书 / 模组原文 / 一局的事件向量）重新嵌入并改记模型名 → 全部一致后把当前模型改为目标、
卸载旧模型。

正确性要点：
- 每个索引在**一个事务**里整体换成新向量和新模型名，读者要么看到旧的（用旧模型查），要么看到
  新的（用新模型查），不会看到半截；检索端按索引记录的模型选嵌入器（见 ``vector_search.grouped_top_k``）。
- 重建期间新建的索引直接用目标模型（``get_embedder()`` 返回写入模型），不会再被重建一遍。
- 目标变了（取消切换＝目标改回当前模型）就丢弃正在做的那个索引，按新目标重来；已经换到
  被放弃的模型上的索引同样「与目标不一致」，会被换回去。
- 进程重启后 ``resume_if_needed()`` 接着做（目标存在设置文件里）。
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app import database
from app.ai import embedding_store
from app.ai.embedding import CATALOG, Embedder, get_embedder, release_embedders
from app.models.event_log import EventLog
from app.models.module import Module, ModuleChunk
from app.models.rulebook import RuleChunk, Rulebook

logger = logging.getLogger(__name__)

#: 每批嵌入多少条后检查一次「目标是否变了」并更新进度
REBUILD_BATCH = 64


@dataclass
class RebuildState:
    running: bool = False
    target: str = ""  # 正在重建到的模型 id
    done: int = 0
    total: int = 0
    current: str = ""  # 正在重建的索引（给设置页看）
    error: str = ""


_state = RebuildState()
_lock = threading.Lock()
_thread: threading.Thread | None = None


def state() -> RebuildState:
    with _lock:
        return RebuildState(**_state.__dict__)


class _TargetChanged(Exception):
    pass


# ---------------------------------------------------------------------------
# 找出与目标不一致的索引
# ---------------------------------------------------------------------------

def _stale_rulebooks(db: Session, name: str):
    return db.query(Rulebook).filter(Rulebook.status == "ready", Rulebook.embed_model != name)


def _stale_modules(db: Session, name: str):
    return db.query(Module).filter(Module.rag_status == "ready", Module.rag_embed_model != name)


def _stale_events(db: Session, name: str):
    return db.query(EventLog).filter(
        EventLog.embedding.is_not(None),
        or_(EventLog.embed_model.is_(None), EventLog.embed_model != name),
    )


def stale_count(db: Session, name: str) -> int:
    """还没换到 ``name`` 的向量条数（重建进度的分母）。"""
    rule = (
        db.query(func.count(RuleChunk.id))
        .join(Rulebook, Rulebook.id == RuleChunk.rulebook_id)
        .filter(Rulebook.status == "ready", Rulebook.embed_model != name)
        .scalar()
    )
    mod = (
        db.query(func.count(ModuleChunk.id))
        .join(Module, Module.id == ModuleChunk.module_id)
        .filter(Module.rag_status == "ready", Module.rag_embed_model != name)
        .scalar()
    )
    ev = _stale_events(db, name).count()
    return int(rule or 0) + int(mod or 0) + int(ev or 0)


# ---------------------------------------------------------------------------
# 重建
# ---------------------------------------------------------------------------

def _embed_all(
    texts: list[str], embedder: Embedder, still_wanted: Callable[[], bool],
) -> list[bytes]:
    out: list[bytes] = []
    for i in range(0, len(texts), REBUILD_BATCH):
        if not still_wanted():
            raise _TargetChanged
        vecs = embedder.embed_passages(texts[i:i + REBUILD_BATCH])
        out.extend(np.asarray(v, dtype=np.float32).tobytes() for v in vecs)
        with _lock:
            _state.done += len(vecs)
    if not still_wanted():
        raise _TargetChanged
    return out


def _rebuild_one(db: Session, embedder: Embedder, still_wanted: Callable[[], bool]) -> bool:
    """重建一个不一致的索引；没有了返回 False。"""
    name = embedder.model_name
    book = _stale_rulebooks(db, name).first()
    if book is not None:
        _set_current(f"规则书《{book.title}》")
        rows = db.query(RuleChunk).filter(RuleChunk.rulebook_id == book.id).order_by(RuleChunk.ordinal).all()
        vecs = _embed_all([r.text for r in rows], embedder, still_wanted)
        for r, v in zip(rows, vecs):
            r.embedding = v
        book.embed_model = name
        db.commit()
        return True
    module = _stale_modules(db, name).first()
    if module is not None:
        _set_current(f"模组《{module.title}》原文")
        rows = db.query(ModuleChunk).filter(ModuleChunk.module_id == module.id).order_by(ModuleChunk.ordinal).all()
        vecs = _embed_all([r.text for r in rows], embedder, still_wanted)
        for r, v in zip(rows, vecs):
            r.embedding = v
        module.rag_embed_model = name
        db.commit()
        return True
    ev = _stale_events(db, name).first()
    if ev is not None:
        # 一局的事件一个事务：同一局里的检索要么全旧要么全新
        _set_current("跑团记录")
        rows = _stale_events(db, name).filter(EventLog.session_id == ev.session_id).order_by(EventLog.sequence_num).all()
        vecs = _embed_all([(r.content or "").strip() for r in rows], embedder, still_wanted)
        for r, v in zip(rows, vecs):
            r.embedding = v
            r.embed_model = name
        db.commit()
        return True
    return False


def _set_current(label: str) -> None:
    with _lock:
        _state.current = label


def _worker() -> None:
    try:
        while True:
            spec = embedding_store.write_model()
            with database.SessionLocal() as db:
                with _lock:
                    _state.target, _state.done, _state.error, _state.current = spec.id, 0, "", ""
                    _state.total = 0
                total = stale_count(db, spec.name)
                with _lock:
                    _state.total = total
                if total:
                    embedder = get_embedder()  # 写入模型；默认模型缺失时会先下载
                    still_wanted = lambda: embedding_store.write_model().id == spec.id  # noqa: E731
                    try:
                        while _rebuild_one(db, embedder, still_wanted):
                            pass
                    except _TargetChanged:
                        db.rollback()
                        logger.info("嵌入模型切换目标已变，放弃当前索引并按新目标重来")
                        continue
            if embedding_store.write_model().id != spec.id:
                continue  # 刚做完时目标又变了
            _finish(spec.id)
            # 退出与「目标是否又变了」的判断放在同一把锁里：request_switch 先存目标再 kick()，
            # 要么这里看到新目标接着做，要么 kick() 看到线程已退出而另起一个，不会漏掉。
            with _lock:
                if embedding_store.write_model().id == spec.id:
                    _release_thread()
                    return
    except Exception as e:  # noqa: BLE001 — 后台线程：失败落状态，下次切换/重启时重试
        logger.exception("嵌入索引重建失败")
        with _lock:
            _state.error = f"{type(e).__name__}: {e}"
    finally:
        with _lock:
            if _thread is threading.current_thread():
                _release_thread()


def _release_thread() -> None:
    """调用方须持有 ``_lock``。"""
    global _thread
    _state.running = False
    _state.current = ""
    _thread = None


def _finish(model_id: str) -> None:
    s = embedding_store.load()
    if s.target == model_id:
        s.active, s.target = model_id, None
        embedding_store.save(s)
        logger.info("嵌入模型已切换为 %s", CATALOG[model_id].label)
    release_embedders({CATALOG[s.active].name})


def kick() -> None:
    """启动后台重建（已在运行则不重复启动；运行中的线程会自己察觉目标变化）。"""
    global _thread
    with _lock:
        if _thread is not None:
            return
        _state.running = True
        _state.error = ""
        _thread = threading.Thread(target=_worker, name="embed-rebuild", daemon=True)
        _thread.start()


def wait(timeout: float | None = None) -> None:
    """等后台重建结束（测试与关停用）。"""
    t = _thread
    if t is not None:
        t.join(timeout)


def request_switch(model_id: str) -> None:
    """切换到 ``model_id``；传当前模型即取消进行中的切换。"""
    spec = CATALOG.get(model_id)
    if spec is None:
        raise ValueError(f"未知模型：{model_id}")
    if not embedding_store.is_installed(spec):
        raise ValueError(f"{spec.label} 尚未下载")
    s = embedding_store.load()
    s.target = None if model_id == s.active else model_id
    embedding_store.save(s)
    kick()


def resume_if_needed() -> None:
    """启动时调用：有未完成的切换、或存在与当前模型不一致的索引，就在后台接着重建。"""
    try:
        s = embedding_store.load()
        spec = embedding_store.write_model()
        if s.target is None:
            if not embedding_store.is_installed(spec):
                return  # 当前模型都没下载：多半是全新安装，没有需要重建的索引
            with database.SessionLocal() as db:
                if not stale_count(db, spec.name):
                    return
        kick()
    except Exception:
        logger.exception("检查嵌入索引是否需要重建时出错（忽略）")
