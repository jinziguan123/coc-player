"""嵌入模型的本机文件与设置：当前/目标模型、下载源、下载任务、删除。

- 设置存 ``embedding_settings.json``（与 ``ai_settings.json`` 同目录）：当前模型 ``active``、
  切换中的目标模型 ``target``、下载源 ``source`` 与 HF 地址 ``hf_endpoint``。
- 模型文件放 ``<数据目录>/models/embed/<模型 id>/``，下载完成写 ``.complete`` 标记；
  下载中的文件在 ``models/embed/.partial/<模型 id>/``，网络失败保留以便续传，取消则清掉。
- 下载源默认 ModelScope（国内直连 HuggingFace 常失败，hf-mirror 也已改为跳回官方）；
  也可选 HuggingFace 并自填地址（镜像或代理），未填时取环境变量 ``HF_ENDPOINT``。
- 每个文件下载完按目录里写死的大小与 sha256 校验，不通过就删掉重来。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

import httpx

from app.ai.embedding import CATALOG, DEFAULT_MODEL_ID, EmbedModelSpec
from app.config import settings
from app.services import task_center

logger = logging.getLogger(__name__)

SETTINGS_FILE = settings.db_path.parent / "embedding_settings.json"
MODELS_DIR = settings.db_path.parent / "models"

Source = Literal["modelscope", "huggingface"]
SOURCES: tuple[str, ...] = ("modelscope", "huggingface")
MODELSCOPE_BASE = "https://www.modelscope.cn/models"
HF_DEFAULT_ENDPOINT = "https://huggingface.co"
DOWNLOAD_RETRIES = 3
_CHUNK = 1 << 20


# ---------------------------------------------------------------------------
# 设置
# ---------------------------------------------------------------------------

@dataclass
class EmbeddingSettings:
    active: str = DEFAULT_MODEL_ID
    target: str | None = None
    source: str = "modelscope"
    hf_endpoint: str = ""


_cache: EmbeddingSettings | None = None
_settings_lock = threading.Lock()


def load() -> EmbeddingSettings:
    global _cache
    with _settings_lock:
        if _cache is None:
            _cache = _read()
        return EmbeddingSettings(**asdict(_cache))


def _read() -> EmbeddingSettings:
    try:
        raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return EmbeddingSettings()
    except (OSError, ValueError):
        logger.exception("嵌入模型设置文件损坏，按默认处理：%s", SETTINGS_FILE)
        return EmbeddingSettings()
    s = EmbeddingSettings()
    if raw.get("active") in CATALOG:
        s.active = raw["active"]
    if raw.get("target") in CATALOG and raw["target"] != s.active:
        s.target = raw["target"]
    if raw.get("source") in SOURCES:
        s.source = raw["source"]
    if isinstance(raw.get("hf_endpoint"), str):
        s.hf_endpoint = raw["hf_endpoint"].strip()
    return s


def save(s: EmbeddingSettings) -> None:
    global _cache
    with _settings_lock:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=SETTINGS_FILE.parent, prefix=".embedding_settings.")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(asdict(s), f, ensure_ascii=False, indent=2)
        os.replace(tmp, SETTINGS_FILE)
        _cache = EmbeddingSettings(**asdict(s))


def active_model() -> EmbedModelSpec:
    return CATALOG[load().active]


def write_model() -> EmbedModelSpec:
    """新建索引与重建的目标：切换中为目标模型，否则为当前模型。"""
    s = load()
    return CATALOG[s.target or s.active]


def set_source(source: str, hf_endpoint: str = "") -> EmbeddingSettings:
    if source not in SOURCES:
        raise ValueError(f"未知下载源：{source}")
    hf_endpoint = hf_endpoint.strip().rstrip("/")
    if hf_endpoint and not hf_endpoint.startswith(("http://", "https://")):
        raise ValueError("HuggingFace 地址需以 http:// 或 https:// 开头")
    s = load()
    s.source, s.hf_endpoint = source, hf_endpoint
    save(s)
    return s


def file_url(spec: EmbedModelSpec, path: str, s: EmbeddingSettings | None = None) -> str:
    s = s or load()
    if s.source == "huggingface":
        endpoint = (s.hf_endpoint or os.environ.get("HF_ENDPOINT") or HF_DEFAULT_ENDPOINT).rstrip("/")
        return f"{endpoint}/{spec.repo}/resolve/main/{path}"
    return f"{MODELSCOPE_BASE}/{spec.repo}/resolve/master/{path}"


# ---------------------------------------------------------------------------
# 本机模型文件
# ---------------------------------------------------------------------------

def _model_dir(spec: EmbedModelSpec) -> Path:
    return MODELS_DIR / "embed" / spec.id


def _partial_dir(spec: EmbedModelSpec) -> Path:
    return MODELS_DIR / "embed" / ".partial" / spec.id


def _legacy_dir(spec: EmbedModelSpec) -> Path | None:
    """旧版 fastembed 内置下载留下的缓存（HF 缓存布局），存量安装直接复用。"""
    if not spec.legacy_cache:
        return None
    root = MODELS_DIR / spec.legacy_cache / "snapshots"
    if not root.is_dir():
        return None
    for snap in sorted(root.iterdir()):
        if all((snap / f.path).exists() for f in spec.files):
            return snap
    return None


def installed_dir(spec: EmbedModelSpec) -> Path | None:
    d = _model_dir(spec)
    if (d / ".complete").exists():
        return d
    return _legacy_dir(spec)


def is_installed(spec: EmbedModelSpec) -> bool:
    return installed_dir(spec) is not None


# ---------------------------------------------------------------------------
# 下载
# ---------------------------------------------------------------------------

class DownloadError(RuntimeError):
    pass


class DownloadCancelled(Exception):
    pass


@dataclass
class DownloadJob:
    model_id: str
    total_bytes: int
    state: str = "downloading"  # downloading / done / failed / cancelled
    done_bytes: int = 0
    error: str = ""
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    #: 「解析进度」抽屉里的任务（task_center）
    task_id: str | None = None


_SETTINGS_LINK = {"to": "/settings?tab=rag", "label": "查看检索模型"}


def _new_job(spec: EmbedModelSpec) -> DownloadJob:
    return DownloadJob(
        model_id=spec.id, total_bytes=spec.size_bytes,
        task_id=task_center.start("embed_download", f"下载检索模型 {spec.label}", stage="下载中", link=_SETTINGS_LINK),
    )


_jobs: dict[str, DownloadJob] = {}
_jobs_lock = threading.Lock()


def _http_client() -> httpx.Client:
    return httpx.Client(
        follow_redirects=True, timeout=httpx.Timeout(30.0, read=120.0), trust_env=True,
    )


def job(model_id: str) -> DownloadJob | None:
    with _jobs_lock:
        return _jobs.get(model_id)


def start_download(model_id: str) -> DownloadJob:
    """后台下载；已在下载则返回现有任务。"""
    spec = CATALOG.get(model_id)
    if spec is None:
        raise ValueError(f"未知模型：{model_id}")
    if is_installed(spec):
        raise ValueError(f"{spec.label} 已下载")
    with _jobs_lock:
        current = _jobs.get(model_id)
        if current is not None and current.state == "downloading":
            return current
        j = _new_job(spec)
        _jobs[model_id] = j
    threading.Thread(
        target=_run, args=(spec, j), name=f"embed-download-{model_id}", daemon=True,
    ).start()
    return j


def cancel_download(model_id: str) -> bool:
    j = job(model_id)
    if j is None or j.state != "downloading":
        return False
    j.cancel.set()
    return True


def ensure_installed(spec: EmbedModelSpec) -> Path:
    """同步下载（默认模型首次使用时）；已有其他线程在下就等它结束。"""
    if (d := installed_dir(spec)) is not None:
        return d
    with _jobs_lock:
        current = _jobs.get(spec.id)
        mine = current is None or current.state != "downloading"
        if mine:
            current = _new_job(spec)
            _jobs[spec.id] = current
    if mine:
        _run(spec, current)
    else:
        while current.state == "downloading":
            current.cancel.wait(0.5)
    d = installed_dir(spec)
    if d is None:
        raise DownloadError(current.error or f"{spec.label} 下载未完成")
    return d


def _run(spec: EmbedModelSpec, j: DownloadJob) -> None:
    try:
        _download(spec, j)
        j.state = "done"
        task_center.finish(j.task_id, "done", stage="下载完成，可到设置页切换")
        logger.info("嵌入模型下载完成：%s", spec.label)
    except DownloadCancelled:
        shutil.rmtree(_partial_dir(spec), ignore_errors=True)
        j.state = "cancelled"
        task_center.finish(j.task_id, "cancelled", stage="已取消下载")
        logger.info("嵌入模型下载已取消：%s", spec.label)
    except Exception as e:  # noqa: BLE001 — 下载失败只落状态，半截文件留着续传
        j.state = "failed"
        j.error = _describe(e)
        task_center.finish(j.task_id, "failed", detail=j.error)
        logger.warning("嵌入模型下载失败：%s：%s", spec.label, j.error)


def _describe(e: Exception) -> str:
    if isinstance(e, DownloadError):
        return str(e)
    if isinstance(e, httpx.TimeoutException):
        return "连接超时，请检查网络或更换下载源"
    if isinstance(e, httpx.TransportError):
        return f"网络错误（{type(e).__name__}），请检查网络或更换下载源"
    return f"{type(e).__name__}: {e}"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _download(spec: EmbedModelSpec, j: DownloadJob) -> None:
    s = load()
    partial = _partial_dir(spec)
    partial.mkdir(parents=True, exist_ok=True)
    with _http_client() as client:
        for mf in spec.files:
            dest = partial / mf.path
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists() and dest.stat().st_size == mf.size:
                if _sha256(dest) == mf.sha256:
                    j.done_bytes += mf.size
                    continue
                dest.unlink()
            url = file_url(spec, mf.path, s)
            base = j.done_bytes
            for attempt in range(1, DOWNLOAD_RETRIES + 1):
                try:
                    _fetch(client, url, dest, mf.size, j, base)
                    break
                except (httpx.TransportError, DownloadError) as e:
                    if j.cancel.is_set():
                        raise DownloadCancelled from e
                    if attempt == DOWNLOAD_RETRIES:
                        raise
                    logger.info("下载 %s 失败（第 %d 次），重试：%s", mf.path, attempt, e)
            if dest.stat().st_size != mf.size or _sha256(dest) != mf.sha256:
                dest.unlink(missing_ok=True)
                raise DownloadError(f"{mf.path} 校验失败（文件不完整或被篡改），请重试")
            j.done_bytes = base + mf.size
    if spec.max_length is not None:
        cfg_path = partial / "tokenizer_config.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg["model_max_length"] = spec.max_length
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    (partial / ".complete").write_text("ok", encoding="utf-8")
    final = _model_dir(spec)
    shutil.rmtree(final, ignore_errors=True)
    os.replace(partial, final)


def _report(j: DownloadJob) -> None:
    task_center.update(
        j.task_id, stage=f"下载中 {j.done_bytes / 1e6:.0f}/{j.total_bytes / 1e6:.0f} MB",
        done=j.done_bytes, total=j.total_bytes,
    )


def _fetch(client: httpx.Client, url: str, dest: Path, size: int, j: DownloadJob, base: int) -> None:
    have = dest.stat().st_size if dest.exists() else 0
    if have > size:
        dest.unlink()
        have = 0
    if have == size:
        return  # 上一次尝试其实已传完（断在收尾阶段），交给调用方校验
    headers = {"Range": f"bytes={have}-"} if have else {}
    with client.stream("GET", url, headers=headers) as r:
        if r.status_code == 206:
            mode = "ab"
        elif r.status_code == 200:
            mode, have = "wb", 0  # 服务器不支持续传：从头下
        else:
            raise DownloadError(f"下载失败：HTTP {r.status_code}（{url}）")
        j.done_bytes = base + have
        with open(dest, mode) as f:
            for chunk in r.iter_bytes(_CHUNK):
                if j.cancel.is_set():
                    raise DownloadCancelled
                f.write(chunk)
                j.done_bytes += len(chunk)
                _report(j)
    if dest.stat().st_size != size:
        raise DownloadError(f"{dest.name} 下载中断（{dest.stat().st_size}/{size} 字节）")


# ---------------------------------------------------------------------------
# 删除
# ---------------------------------------------------------------------------

def delete_model(model_id: str) -> None:
    spec = CATALOG.get(model_id)
    if spec is None:
        raise ValueError(f"未知模型：{model_id}")
    s = load()
    if model_id in (s.active, s.target):
        raise ValueError(f"{spec.label} 正在使用中，不能删除")
    j = job(model_id)
    if j is not None and j.state == "downloading":
        raise ValueError(f"{spec.label} 正在下载，请先取消")
    from app.ai.embedding import release_embedders, SPECS_BY_NAME

    release_embedders({n for n in SPECS_BY_NAME if n != spec.name})
    shutil.rmtree(_model_dir(spec), ignore_errors=True)
    shutil.rmtree(_partial_dir(spec), ignore_errors=True)
    if spec.legacy_cache:
        shutil.rmtree(MODELS_DIR / spec.legacy_cache, ignore_errors=True)
    with _jobs_lock:
        _jobs.pop(model_id, None)
