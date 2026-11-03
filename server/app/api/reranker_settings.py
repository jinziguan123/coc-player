"""精排模型管理接口：列表、下载/取消、删除、开关（仅房主本机）。

与检索模型分开管理：精排只在 KP 主动查阅模组原文时生效，开关存在自己的设置文件里
（见 app.ai.reranker）；下载器与下载源与检索模型共用。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.ai import embedding_store, reranker
from app.ai.embedding import RERANKERS
from app.api.deps import require_local_client
from app.api.model_views import EmbedModelView, model_view

router = APIRouter(
    prefix="/api/settings/reranker", tags=["settings"],
    dependencies=[Depends(require_local_client)],
)


class RerankerSettingsView(BaseModel):
    models: list[EmbedModelView]
    #: KP 主动查阅模组原文时是否精排
    enabled: bool


class RerankerUpdate(BaseModel):
    enabled: bool


def _view() -> RerankerSettingsView:
    enabled = reranker.is_enabled()
    return RerankerSettingsView(
        models=[
            model_view(spec, active=enabled and embedding_store.is_installed(spec))
            for spec in RERANKERS.values()
        ],
        enabled=enabled,
    )


def _reranker_model(model_id: str) -> str:
    if model_id not in RERANKERS:
        raise HTTPException(status_code=404, detail=f"未知精排模型：{model_id}")
    return model_id


@router.get("", response_model=RerankerSettingsView)
def get_reranker_settings():
    return _view()


@router.put("", response_model=RerankerSettingsView)
def update_reranker(body: RerankerUpdate):
    """开关精排；开启要求精排模型已下载。"""
    try:
        reranker.set_enabled(body.enabled)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _view()


@router.post("/models/{model_id}/download", response_model=RerankerSettingsView)
def download_model(model_id: str):
    try:
        embedding_store.start_download(_reranker_model(model_id))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _view()


@router.post("/models/{model_id}/cancel", response_model=RerankerSettingsView)
def cancel_download(model_id: str):
    embedding_store.cancel_download(_reranker_model(model_id))
    return _view()


@router.delete("/models/{model_id}", response_model=RerankerSettingsView)
def delete_model(model_id: str):
    try:
        embedding_store.delete_model(_reranker_model(model_id))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _view()
