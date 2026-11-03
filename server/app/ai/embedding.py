"""文本嵌入抽象层。

与具体嵌入实现解耦（贴合平台「Provider 抽象」决策）：上层（规则书/模组原文/事件索引与检索）
只依赖 ``Embedder`` 接口。实现走纯 ONNX（fastembed，无 torch），全本地离线。

可选模型见 ``CATALOG``：默认 ``bge-small-zh-v1.5``（512 维，约 95MB）；房主可在设置页下载
更强的 ``bge-large-zh-v1.5``（int8，1024 维，约 327MB）或 ``bge-m3``（int8，1024 维，约 587MB）
并热切换。模型文件由 ``embedding_store`` 自行下载到数据目录（不走 fastembed 内置下载：
国内直连 HF 常失败，且内置下载没有进度与取消）。

**每个向量索引都记录建它的模型名**（``Embedder.model_name``，即 ``EmbedModelSpec.name``）。
查询时必须用同一个模型嵌入查询——不同模型的向量空间互不相通，维度也可能不同。
``embedder_for(name)`` 按索引记录的模型名取嵌入器；切换模型的后台重建期间新旧模型同时驻留。
"""

from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

#: 建入库向量时一批喂给 ONNX 的条数。fastembed 默认 256：512 token 满长时 onnxruntime 一次
#: 要几 GB 的激活内存（实测 small 建 2939 块规则书峰值 6.8GB），16 时峰值 0.75GB 且更快。
PASSAGE_BATCH_SIZE = 16

#: bge 中文 v1.5 官方的检索指令前缀：只加在查询侧，passage 侧不加。
BGE_ZH_QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："


@dataclass(frozen=True)
class ModelFile:
    path: str  # 仓库内相对路径（下载后在模型目录内保持同样的相对路径）
    size: int
    sha256: str


@dataclass(frozen=True)
class EmbedModelSpec:
    """一个可选嵌入模型的全部静态信息。"""

    id: str  # 设置与接口里用的短 id
    name: str  # 写进索引的模型名（换模型时据此识别旧索引）
    label: str
    description: str
    dim: int
    repo: str  # ModelScope 与 HuggingFace 上的仓库名（两边同名）
    files: tuple[ModelFile, ...]
    model_file: str  # ONNX 主文件（files 之一）
    query_prefix: str = ""
    #: 下载后改写 tokenizer_config.json 的 model_max_length（Xenova 的 large 写的是 1e30，
    #: fastembed 截断时会整数溢出）。None = 不改。
    max_length: int | None = None
    #: 旧版 fastembed 内置下载的缓存目录名（存量安装已有的模型，直接复用，不重下）
    legacy_cache: str | None = None
    recommended: bool = False
    #: 给设置页看的资源与效果说明（来自本机评测，见设计文档）
    index_speed: str = ""
    memory: str = ""
    #: embedding＝检索（嵌入）模型；reranker＝精排模型（cross-encoder，见 app.ai.reranker）
    kind: str = "embedding"

    @property
    def size_bytes(self) -> int:
        return sum(f.size for f in self.files)


_TOKENIZER_FILES = ("config.json", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json")

CATALOG: dict[str, EmbedModelSpec] = {
    s.id: s
    for s in (
        EmbedModelSpec(
            id="bge-small-zh-v1.5",
            name="BAAI/bge-small-zh-v1.5",
            label="bge-small-zh-v1.5（默认）",
            description="轻量中文模型，建索引最快、内存最省。规则书与往事回忆的命中率明显低于大模型。",
            dim=512,
            repo="Qdrant/bge-small-zh-v1.5",
            files=(
                ModelFile("config.json", 739, "9088751d39abbf86ec3d19ffca92ad62ad19075f7e59712e6c71217fa125d1d3"),
                ModelFile("tokenizer.json", 439125, "48cea5d44424912a6fd1ea647bf4fe50b55ab8b1e5879c3275f80e339e8fae26"),
                ModelFile("tokenizer_config.json", 367, "e6f3b96db926a37d4039995fbf5ad17de158dfb8f6343d607e4dbaad18d75f5a"),
                ModelFile("special_tokens_map.json", 125, "b6d346be366a7d1d48332dbc9fdf3bf8960b5d879522b7799ddba59e76237ee3"),
                ModelFile("model_optimized.onnx", 94781076, "1294ea4b6331115a353d81f96b85e8c8d7fdcc284453d5b2fab5b016230aad38"),
            ),
            model_file="model_optimized.onnx",
            # 本机评测里 small 加前缀反而略差，维持不加（与存量索引的建法一致）
            legacy_cache="models--Qdrant--bge-small-zh-v1.5",
            index_speed="约 40 块/秒",
            memory="约 0.8GB",
        ),
        EmbedModelSpec(
            id="bge-large-zh-v1.5-int8",
            name="BAAI/bge-large-zh-v1.5@int8",
            label="bge-large-zh-v1.5（int8，推荐）",
            description="中文检索效果最好的一档：规则书的口语化问法与往事回忆的命中率比默认模型明显更高。",
            dim=1024,
            repo="Xenova/bge-large-zh-v1.5",
            files=(
                ModelFile("config.json", 940, "b8a4dce1dfa153b714eb25c75b18238ef2b12e4755f998457f60cd872483be66"),
                ModelFile("tokenizer.json", 439124, "7dfbf1966ebf99d471c3796e9b457329d2b2182b817e144f1e904b957745c839"),
                ModelFile("tokenizer_config.json", 394, "e1790949631401af1bfb6c9c7aeec7fcf612e274d73579d99f704faea40c8ba7"),
                ModelFile("special_tokens_map.json", 125, "b6d346be366a7d1d48332dbc9fdf3bf8960b5d879522b7799ddba59e76237ee3"),
                ModelFile("onnx/model_int8.onnx", 326164024, "8a3f371a7e535e25d3d5a0ff0c0501a605ef0b62577800d2bf4b1fc76d6cbcf1"),
            ),
            model_file="onnx/model_int8.onnx",
            query_prefix=BGE_ZH_QUERY_PREFIX,
            max_length=512,
            recommended=True,
            index_speed="约 5 块/秒",
            memory="约 2.6GB",
        ),
        EmbedModelSpec(
            id="bge-m3-int8",
            name="BAAI/bge-m3@int8",
            label="bge-m3（int8）",
            description="多语言模型：规则书与模组原文检索最好，中英混排更稳；往事回忆不如 large，内存占用最高。",
            dim=1024,
            repo="Xenova/bge-m3",
            files=(
                ModelFile("config.json", 770, "734a79bf12d388c1467a4e3ab625f45de7f6906cffcfb93a1eca1787504bed95"),
                ModelFile("tokenizer.json", 17082821, "6710678b12670bc442b99edc952c4d996ae309a7020c1fa0096dd245c2faf790"),
                ModelFile("tokenizer_config.json", 1173, "7e4c1cc848840aeccdd763458c18dd525eb0f795c992e00ebe9c28554e7db2d4"),
                ModelFile("special_tokens_map.json", 964, "8c785abebea9ae3257b61681b4e6fd8365ceafde980c21970d001e834cf10835"),
                ModelFile("onnx/model_int8.onnx", 568456694, "a206e10e995aa2a833924bcd725ba5dd6c3425cd34bac3cf2b5677cd2a1c51d6"),
            ),
            model_file="onnx/model_int8.onnx",
            index_speed="约 6 块/秒",
            memory="约 5.5GB",
        ),
    )
}
DEFAULT_MODEL_ID = "bge-small-zh-v1.5"
SPECS_BY_NAME = {s.name: s for s in CATALOG.values()}

#: 精排模型（cross-encoder）。本机评测：模组原文 15 条线索查询 nDCG@3 0.551→0.692、完整答案 12→14；
#: 规则书（已按词条建索引 + 标题命中）上无稳定提升，故只用于 KP 主动查阅模组原文。
#: 20 个约 500 字候选在本机 CPU 上约 3.6 秒，所以不进每轮的被动检索。
RERANKERS: dict[str, EmbedModelSpec] = {
    s.id: s
    for s in (
        EmbedModelSpec(
            id="bge-reranker-v2-m3-int8",
            name="BAAI/bge-reranker-v2-m3@int8",
            label="bge-reranker-v2-m3（int8）",
            description="KP 主动查阅模组原文时，把向量检索取回的候选按与问题的相关度重新排序，"
                        "让「找到了但排在后面」的那段原文排到前面。",
            dim=0,
            repo="onnx-community/bge-reranker-v2-m3-ONNX",
            files=(
                ModelFile("config.json", 848, "122e922dcfed6503c8721e6fe1daf090340c3d95ca7f3aa3a72730b321a51cfd"),
                ModelFile("tokenizer.json", 17082900, "8bf8afbfd11306bd872018c53bfdf2e160a56f8edbcf49933324404791c148d3"),
                ModelFile("tokenizer_config.json", 1203, "b87c8703482b0300d3da30e201519aa641f6a450f5eb5bf1e624afbf70c74d80"),
                ModelFile("special_tokens_map.json", 964, "8c785abebea9ae3257b61681b4e6fd8365ceafde980c21970d001e834cf10835"),
                ModelFile("onnx/model_int8.onnx", 570727094, "912fc1215c2dbff6499700534bd8d31253af01573861abbfc43afd1fab6cce5d"),
            ),
            model_file="onnx/model_int8.onnx",
            # 候选是不超过 500 字的原文块，1024 token 足够；不改的话按 8192 截断，长输入徒增耗时
            max_length=1024,
            recommended=True,
            index_speed="每次查阅约多 2～4 秒",
            memory="约 1GB",
            kind="reranker",
        ),
    )
}
def model_spec(model_id: str) -> EmbedModelSpec | None:
    """检索模型或精排模型（下载 / 删除 / 是否已下载对两类通用）。"""
    return CATALOG.get(model_id) or RERANKERS.get(model_id)

# 兼容旧引用（DEFAULT_EMBED_MODEL 是写进存量索引的那个模型名）
DEFAULT_EMBED_MODEL = CATALOG[DEFAULT_MODEL_ID].name
DEFAULT_EMBED_DIM = CATALOG[DEFAULT_MODEL_ID].dim


class EmbedderUnavailable(RuntimeError):
    """所需模型未下载或无法加载。"""


class Embedder(ABC):
    """文本嵌入提供者抽象接口。"""

    #: 向量维度（持久化/校验用）
    dim: int
    #: 模型标识：写进每个索引，换模型时据此识别旧索引
    model_name: str

    @abstractmethod
    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        """嵌入入库文档片段（passage 语义）。"""

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        """嵌入检索查询（query 语义；需要指令前缀的模型由实现自行加上）。"""


class FastEmbedEmbedder(Embedder):
    """fastembed(ONNX) 实现：从 ``model_dir`` 加载 ``spec`` 描述的模型。"""

    def __init__(self, spec: EmbedModelSpec, model_dir: Path):
        self.spec = spec
        self.model_name = spec.name
        self.dim = spec.dim
        self._dir = model_dir
        self._model = None  # 懒加载：首次嵌入时才构造
        self._lock = threading.Lock()

    def _ensure(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from fastembed import TextEmbedding

                    register_custom_model(self.spec)
                    self._model = TextEmbedding(
                        model_name=_fastembed_name(self.spec), specific_model_path=str(self._dir),
                    )
        return self._model

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._ensure()
        return [v.tolist() for v in model.passage_embed(texts, batch_size=PASSAGE_BATCH_SIZE)]

    def embed_query(self, text: str) -> list[float]:
        model = self._ensure()
        return next(iter(model.query_embed([self.spec.query_prefix + text]))).tolist()


def _fastembed_name(spec: EmbedModelSpec) -> str:
    # 用本项目自己的命名空间注册，避免与 fastembed 内置同名模型（如 BAAI/bge-small-zh-v1.5）冲突
    return f"coc-player/{spec.id}"


def custom_model_kwargs(spec: EmbedModelSpec) -> dict:
    """``TextEmbedding.add_custom_model`` 的参数（单测断言用）。"""
    from fastembed.common.model_description import ModelSource, PoolingType

    return dict(
        model=_fastembed_name(spec),
        pooling=PoolingType.CLS,
        normalization=True,
        # 文件由 embedding_store 下载、经 specific_model_path 加载，这里的来源不会被用到，
        # 但 fastembed 要求至少给一个
        sources=ModelSource(hf=spec.repo),
        dim=spec.dim,
        model_file=spec.model_file,
        size_in_gb=round(spec.size_bytes / 1e9, 2),
    )


_registered: set[str] = set()
_register_lock = threading.Lock()


def register_custom_model(spec: EmbedModelSpec) -> None:
    from fastembed import TextEmbedding

    with _register_lock:
        if spec.id in _registered:
            return
        try:
            TextEmbedding.add_custom_model(**custom_model_kwargs(spec))
        except ValueError:
            pass  # 已注册（同进程重复加载）
        _registered.add(spec.id)


# ---------------------------------------------------------------------------
# 进程级嵌入器缓存：按模型名各一个实例，切换期间新旧模型可同时驻留
# ---------------------------------------------------------------------------

_embedders: dict[str, Embedder] = {}
_embedders_lock = threading.Lock()


def _load(name: str) -> Embedder | None:
    from app.ai import embedding_store

    spec = SPECS_BY_NAME.get(name)
    if spec is None:
        return None
    model_dir = embedding_store.installed_dir(spec)
    if model_dir is None:
        return None
    return FastEmbedEmbedder(spec, model_dir)


def embedder_for(name: str) -> Embedder | None:
    """按索引记录的模型名取嵌入器；模型未知或未下载返回 None（调用方据此跳过该索引）。"""
    with _embedders_lock:
        emb = _embedders.get(name)
        if emb is None:
            emb = _load(name)
            if emb is not None:
                _embedders[name] = emb
        return emb


def get_embedder() -> Embedder:
    """写入用的嵌入器：切换中为目标模型，否则为当前模型。新建索引一律用它。"""
    from app.ai import embedding_store

    spec = embedding_store.write_model()
    emb = embedder_for(spec.name)
    if emb is None and spec.id == DEFAULT_MODEL_ID:
        # 默认模型保持「开箱即用」：首次用到时同步下载一次（与旧版 fastembed 自动下载行为一致）
        embedding_store.ensure_installed(spec)
        emb = embedder_for(spec.name)
    if emb is None:
        raise EmbedderUnavailable(f"嵌入模型 {spec.label} 尚未下载，请到设置页「检索」下载")
    return emb


def release_embedders(keep: set[str]) -> None:
    """卸载 ``keep`` 之外的嵌入器（切换完成后释放旧模型的内存）。"""
    with _embedders_lock:
        for name in list(_embedders):
            if name not in keep:
                del _embedders[name]
