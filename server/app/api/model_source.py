"""模型下载源（检索模型与精排模型共用，仅房主本机）。"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.ai import embedding_store
from app.api.deps import require_local_client

router = APIRouter(
    prefix="/api/settings/model-source", tags=["settings"],
    dependencies=[Depends(require_local_client)],
)


class ModelSourceView(BaseModel):
    #: modelscope（国内推荐）/ huggingface
    source: str
    #: 选 HuggingFace 时的镜像或代理地址；空＝取环境变量 HF_ENDPOINT 或官方地址
    hf_endpoint: str


def _view() -> ModelSourceView:
    s = embedding_store.load()
    return ModelSourceView(source=s.source, hf_endpoint=s.hf_endpoint)


@router.get("", response_model=ModelSourceView)
def get_model_source():
    return _view()


@router.put("", response_model=ModelSourceView)
def update_model_source(body: ModelSourceView):
    try:
        embedding_store.set_source(body.source, body.hf_endpoint)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _view()
