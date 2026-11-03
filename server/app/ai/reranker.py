"""精排（cross-encoder）：把向量检索取回的候选按与查询的相关度重新排序。

只用在 KP **主动**查阅模组原文时（``module_lookup`` / 真人 KP 工作台的模组检索）：
本机评测里它把模组原文的完整答案从 12/15 提到 14/15，主要救回「找到了但排在后面」的段落；
但 20 个候选要约 3.6 秒，放进每轮的被动检索会让每轮都变慢。规则书已按词条建索引并做了
标题命中，精排在那里没有稳定提升，不接。

模型由 ``embedding_store`` 下载（与检索模型同一套下载器），设置页开关；未开启或未下载时
``get_reranker()`` 返回 None，调用方照旧用向量排序。
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from app.ai import embedding_store
from app.ai.embedding import RERANKERS, EmbedModelSpec

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


_registered: set[str] = set()
_instance: Reranker | None = None
_lock = threading.Lock()


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
    if not embedding_store.load().reranker_enabled:
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
