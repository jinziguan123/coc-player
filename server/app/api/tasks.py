"""后台任务汇总（「解析进度」抽屉）：模组上传解析 + 任务中心里的其他长任务（仅房主本机）。"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api import modules as modules_api
from app.api.deps import require_local_client
from app.services import task_center

router = APIRouter(prefix="/api/tasks", tags=["tasks"], dependencies=[Depends(require_local_client)])

_MODULE_PREFIX = "module:"


class TaskLink(BaseModel):
    to: str
    label: str


class BackgroundTask(BaseModel):
    id: str
    #: module（模组上传解析）/ rulebook / module_index / embed_download / embed_rebuild
    kind: str
    title: str
    status: str  # running / done / failed / cancelled
    stage: str
    #: 有总量时的百分比；None＝阶段性步骤，前端显示不定进度
    percent: int | None = None
    detail: str = ""
    link: TaskLink | None = None
    created_at: float


def _module_job(job: dict) -> BackgroundTask:
    result = job.get("result") or {}
    files = job.get("files") or []
    done = job["status"] == "done"
    return BackgroundTask(
        id=_MODULE_PREFIX + job["id"], kind="module",
        title=result.get("title") or "、".join(files) or "未命名文件",
        status=job["status"], stage=job.get("stage") or "",
        percent=job.get("percent") if job["status"] == "running" else (100 if done else None),
        detail=job.get("detail") or "",
        link=TaskLink(to=f"/modules/{result['id']}", label="查看模组") if done and result.get("id") else None,
        created_at=job.get("created_at") or 0,
    )


@router.get("", response_model=list[BackgroundTask])
def list_tasks():
    """最近的后台任务，新的在前；进行中的任务在此显示阶段与进度。"""
    items = [_module_job(j) for j in list(modules_api._upload_jobs.values())]
    items += [BackgroundTask(**t) for t in task_center.tasks()]
    return sorted(items, key=lambda t: t.created_at, reverse=True)


@router.delete("/{task_id}")
def dismiss_task(task_id: str):
    """从列表移除一个已结束的任务；进行中的不能移除。"""
    if task_id.startswith(_MODULE_PREFIX):
        job = modules_api._upload_jobs.get(task_id[len(_MODULE_PREFIX):])
        if job is None:
            raise HTTPException(404, "任务不存在")
        if job["status"] == "running":
            raise HTTPException(409, "任务仍在进行中")
        modules_api._upload_jobs.pop(job["id"], None)
        return {"ok": True}
    if not task_center.dismiss(task_id):
        raise HTTPException(404, "任务不存在或仍在进行中")
    return {"ok": True}
