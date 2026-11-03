"""检索模型与精排模型设置页共用的模型条目视图（下载状态、是否已下载……）。"""
from pydantic import BaseModel

from app.ai import embedding_store
from app.ai.embedding import EmbedModelSpec


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


def model_view(spec: EmbedModelSpec, *, active: bool, target: bool = False) -> EmbedModelView:
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
