"""检索（嵌入）模型管理接口：列表、下载/取消、删除、切换、重建（仅房主本机）。

精排模型见 ``reranker_settings``，两类模型共用的下载源见 ``model_source``。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import database
from app.ai import embedding_store
from app.ai.embedding import CATALOG
from app.api.deps import require_local_client
from app.api.model_views import EmbedModelView, model_view
from app.services import embedding_switch

# 只限本机：下载与重建占用房主的流量、磁盘与 CPU，客人不该能动。
router = APIRouter(
    prefix="/api/settings/embedding", tags=["settings"],
    dependencies=[Depends(require_local_client)],
)


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
    rebuild: RebuildView
    #: 与当前写入模型不一致、还需重建的向量条数（重建没在跑时用于提示「有旧索引待重建」）
    stale: int


class SwitchRequest(BaseModel):
    model_id: str


def _view() -> EmbeddingSettingsView:
    s = embedding_store.load()
    models = [
        model_view(spec, active=spec.id == s.active, target=spec.id == s.target)
        for spec in CATALOG.values()
    ]
    st = embedding_switch.state()
    with database.SessionLocal() as db:
        stale = embedding_switch.stale_count(db, embedding_store.write_model().name)
    return EmbeddingSettingsView(
        models=models, active=s.active, target=s.target,
        rebuild=RebuildView(**st.__dict__), stale=stale,
    )


def _embedding_model(model_id: str) -> str:
    if model_id not in CATALOG:
        raise HTTPException(status_code=404, detail=f"未知检索模型：{model_id}")
    return model_id


@router.get("", response_model=EmbeddingSettingsView)
def get_embedding_settings():
    return _view()


@router.post("/models/{model_id}/download", response_model=EmbeddingSettingsView)
def download_model(model_id: str):
    try:
        embedding_store.start_download(_embedding_model(model_id))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _view()


@router.post("/models/{model_id}/cancel", response_model=EmbeddingSettingsView)
def cancel_download(model_id: str):
    embedding_store.cancel_download(_embedding_model(model_id))
    return _view()


@router.delete("/models/{model_id}", response_model=EmbeddingSettingsView)
def delete_model(model_id: str):
    try:
        embedding_store.delete_model(_embedding_model(model_id))
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


@router.post("/rebuild", response_model=EmbeddingSettingsView)
def retry_rebuild():
    """重建失败后手动重试（或补建与当前模型不一致的旧索引）。"""
    embedding_switch.kick()
    return _view()
