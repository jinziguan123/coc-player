"""精排（cross-encoder）：把向量检索取回的候选按与查询的相关度重新排序。

只用在 KP **主动**查阅模组原文时（``module_lookup`` / 真人 KP 工作台的模组检索）：
本机评测里它把模组原文的完整答案从 12/15 提到 14/15，主要救回「找到了但排在后面」的段落；
但 20 个候选要约 3.6 秒，放进每轮的被动检索会让每轮都变慢。规则书已按词条建索引并做了
标题命中，精排在那里没有稳定提升，不接。

模型由 ``embedding_store`` 的下载器下载（与检索模型共用下载器与下载源），开关单独存在
``reranker_settings.json``，与检索模型的设置互不相干；未开启或未下载时 ``get_reranker()``
返回 None，调用方照旧用向量排序。
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path

from app.ai import embedding_store
from app.ai.embedding import RERANKERS, EmbedModelSpec
from app.config import settings

logger = logging.getLogger(__name__)


class Reranker:
    def __init__(self, spec: EmbedModelSpec, model_dir: Path):
        self.spec = spec
        self._dir = model_dir
        self._model = None
        self._lock = threading.Lock()

    def _ensure(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from fastembed.rerank.cross_encoder import TextCrossEncoder

                    register_custom_model(self.spec)
                    self._model = TextCrossEncoder(_fastembed_name(self.spec), specific_model_path=str(self._dir))
        return self._model

    def scores(self, query: str, documents: list[str]) -> list[float]:
        """每个文档与查询的相关度分（越大越相关），顺序与 ``documents`` 一致。"""
        if not documents:
            return []
        return [float(x) for x in self._ensure().rerank(query, documents, batch_size=32)]


def _fastembed_name(spec: EmbedModelSpec) -> str:
    return f"coc-player/{spec.id}"


def custom_model_kwargs(spec: EmbedModelSpec) -> dict:
    """``TextCrossEncoder.add_custom_model`` 的参数（单测断言用）。"""
    from fastembed.common.model_description import ModelSource

    return dict(
        model=_fastembed_name(spec),
        # 文件由 embedding_store 下载、经 specific_model_path 加载，这里的来源不会被用到
        sources=ModelSource(hf=spec.repo),
        model_file=spec.model_file,
        size_in_gb=round(spec.size_bytes / 1e9, 2),
    )


SETTINGS_FILE = settings.db_path.parent / "reranker_settings.json"

_registered: set[str] = set()
_instance: Reranker | None = None
_lock = threading.Lock()
_enabled: bool | None = None  # 设置缓存（所有写入都经本模块，缓存即权威）


def is_enabled() -> bool:
    global _enabled
    with _lock:
        if _enabled is None:
            try:
                _enabled = json.loads(SETTINGS_FILE.read_text(encoding="utf-8")).get("enabled") is True
            except FileNotFoundError:
                _enabled = _migrate_legacy_setting()
            except (OSError, ValueError):
                logger.exception("精排设置文件损坏，按关闭处理：%s", SETTINGS_FILE)
                _enabled = False
        return _enabled


def _migrate_legacy_setting() -> bool:
    """早先精排开关存在 embedding_settings.json 的 ``reranker_enabled`` 里：读出来写进自己的文件。

    必须立刻落盘——检索模型设置下次保存时会丢掉这个旧键，到时就无从迁移了。调用方持有 ``_lock``。
    """
    try:
        legacy = json.loads(embedding_store.SETTINGS_FILE.read_text(encoding="utf-8")).get("reranker_enabled") is True
    except (OSError, ValueError):
        return False
    if legacy:
        _write(True)
        logger.info("精排开关已从检索模型设置迁移到 %s", SETTINGS_FILE.name)
    return legacy


def _write(enabled: bool) -> None:
    """调用方持有 ``_lock``。"""
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=SETTINGS_FILE.parent, prefix=".reranker_settings.")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"enabled": enabled}, f)
    os.replace(tmp, SETTINGS_FILE)


def set_enabled(enabled: bool) -> None:
    """开关精排；开启要求精排模型已下载。"""
    global _enabled
    if enabled and installed_spec() is None:
        raise ValueError("精排模型尚未下载")
    with _lock:
        _write(enabled)
        _enabled = enabled
    if not enabled:
        release()


def on_model_deleted() -> None:
    """精排模型被删除：开关跟着关掉、卸载模型。"""
    if is_enabled():
        set_enabled(False)
    release()


def register_custom_model(spec: EmbedModelSpec) -> None:
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    with _lock:
        if spec.id in _registered:
            return
        try:
            TextCrossEncoder.add_custom_model(**custom_model_kwargs(spec))
        except ValueError:
            pass  # 已注册（同进程重复加载）
        _registered.add(spec.id)


def installed_spec() -> EmbedModelSpec | None:
    return next((s for s in RERANKERS.values() if embedding_store.is_installed(s)), None)


def get_reranker() -> Reranker | None:
    """已开启且已下载时返回精排器，否则 None。"""
    global _instance
    if not is_enabled():
        return None
    spec = installed_spec()
    if spec is None:
        return None
    with _lock:
        if _instance is None or _instance.spec.id != spec.id:
            _instance = Reranker(spec, embedding_store.installed_dir(spec))
        return _instance


def release() -> None:
    """卸载精排模型（关闭或删除时释放内存）。"""
    global _instance
    with _lock:
        _instance = None
