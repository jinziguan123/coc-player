"""检索（嵌入）模型管理接口：列表、下载/取消、删除、切换、下载源（仅房主本机）。"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import database
from app.ai import embedding_store
from app.ai.embedding import CATALOG, RERANKERS, EmbedModelSpec
from app.api.deps import require_local_client
from app.services import embedding_switch

# 只限本机：下载与重建占用房主的流量、磁盘与 CPU，客人不该能动。
router = APIRouter(
    prefix="/api/settings/embedding", tags=["settings"],
    dependencies=[Depends(require_local_client)],
)


class DownloadView(BaseModel):
    state: str  # downloading / done / failed / cancelled
    done_bytes: int
    total_bytes: int
    error: str = ""


class EmbedModelView(BaseModel):
    id: str
    label: str
    description: str
    dim: int
    size_bytes: int
    recommended: bool
    index_speed: str
    memory: str
    installed: bool
    active: bool
    target: bool
    download: DownloadView | None = None


class RebuildView(BaseModel):
    running: bool
    target: str
    done: int
    total: int
    current: str
    error: str


class EmbeddingSettingsView(BaseModel):
    models: list[EmbedModelView]
    active: str
    target: str | None
    source: str
    hf_endpoint: str
    rebuild: RebuildView
    #: 与当前写入模型不一致、还需重建的向量条数（重建没在跑时用于提示「有旧索引待重建」）
    stale: int
    #: 精排模型（KP 主动查阅模组原文时用）
    rerankers: list[EmbedModelView] = []
    reranker_enabled: bool = False


class SourceUpdate(BaseModel):
    source: str
    hf_endpoint: str = ""


class SwitchRequest(BaseModel):
    model_id: str


class RerankerUpdate(BaseModel):
    enabled: bool


def _model_view(spec: EmbedModelSpec, *, active: bool, target: bool) -> EmbedModelView:
    j = embedding_store.job(spec.id)
    return EmbedModelView(
        id=spec.id, label=spec.label, description=spec.description, dim=spec.dim,
        size_bytes=spec.size_bytes, recommended=spec.recommended,
        index_speed=spec.index_speed, memory=spec.memory,
        installed=embedding_store.is_installed(spec), active=active, target=target,
        download=DownloadView(
            state=j.state, done_bytes=j.done_bytes, total_bytes=j.total_bytes, error=j.error,
        ) if j is not None else None,
    )


def _view() -> EmbeddingSettingsView:
    s = embedding_store.load()
    models = [
        _model_view(spec, active=spec.id == s.active, target=spec.id == s.target)
        for spec in CATALOG.values()
    ]
    rerankers = [
        _model_view(spec, active=s.reranker_enabled and embedding_store.is_installed(spec), target=False)
        for spec in RERANKERS.values()
    ]
    st = embedding_switch.state()
    with database.SessionLocal() as db:
        stale = embedding_switch.stale_count(db, embedding_store.write_model().name)
    return EmbeddingSettingsView(
        models=models, active=s.active, target=s.target, source=s.source,
        hf_endpoint=s.hf_endpoint, rebuild=RebuildView(**st.__dict__), stale=stale,
        rerankers=rerankers, reranker_enabled=s.reranker_enabled,
    )


@router.get("", response_model=EmbeddingSettingsView)
def get_embedding_settings():
    return _view()


@router.put("/source", response_model=EmbeddingSettingsView)
def update_source(body: SourceUpdate):
    try:
        embedding_store.set_source(body.source, body.hf_endpoint)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _view()


@router.post("/models/{model_id}/download", response_model=EmbeddingSettingsView)
def download_model(model_id: str):
    try:
        embedding_store.start_download(model_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _view()


@router.post("/models/{model_id}/cancel", response_model=EmbeddingSettingsView)
def cancel_download(model_id: str):
    embedding_store.cancel_download(model_id)
    return _view()


@router.delete("/models/{model_id}", response_model=EmbeddingSettingsView)
def delete_model(model_id: str):
    try:
        embedding_store.delete_model(model_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _view()


@router.post("/switch", response_model=EmbeddingSettingsView)
def switch_model(body: SwitchRequest):
    """切换检索模型（后台重建索引）；传当前模型即取消进行中的切换。"""
    try:
        embedding_switch.request_switch(body.model_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _view()


@router.put("/reranker", response_model=EmbeddingSettingsView)
def update_reranker(body: RerankerUpdate):
    """开关精排（KP 主动查阅模组原文时生效）；开启要求精排模型已下载。"""
    try:
        embedding_store.set_reranker_enabled(body.enabled)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _view()


@router.post("/rebuild", response_model=EmbeddingSettingsView)
def retry_rebuild():
    """重建失败后手动重试（或补建与当前模型不一致的旧索引）。"""
    embedding_switch.kick()
    return _view()
