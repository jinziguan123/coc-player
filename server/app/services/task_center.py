"""后台任务中心：所有在后台跑、要看进度的长任务都在这里登记，供「解析进度」抽屉统一展示。

登记的任务：规则书导入（rulebook）、模组原文建索引（module_index）、检索模型下载
（embed_download）、切换检索模型后的索引重建（embed_rebuild）。模组上传解析有自己的任务表
（api/modules.py，接口与测试都依赖它），由 ``/api/tasks`` 汇总时一并映射进来。

只在内存里：重启后列表清空（各任务自身的恢复逻辑见各自模块）。已结束的保留最近若干条，
供前端弹完成提示、给出跳转链接，用户可手动移除。
"""
from __future__ import annotations

import itertools
import threading
import time
from dataclasses import asdict, dataclass, field

#: 已结束的任务最多保留这么多条（进行中的不受限）
MAX_FINISHED = 30


@dataclass
class Task:
    id: str
    kind: str
    title: str
    status: str = "running"  # running / done / failed / cancelled
    stage: str = "排队中"
    done: int = 0
    total: int = 0
    detail: str = ""
    #: 完成后的跳转：{"to": 前端路由, "label": 按钮文字}
    link: dict | None = None
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    @property
    def percent(self) -> int | None:
        """有总量时的百分比；没有（阶段性、无法计量的步骤）为 None，前端显示不定进度。"""
        if self.status != "running":
            return 100 if self.status == "done" else None
        return min(99, int(self.done * 100 / self.total)) if self.total else None

    def view(self) -> dict:
        return {**asdict(self), "percent": self.percent}


_lock = threading.Lock()
_tasks: dict[str, Task] = {}
_seq = itertools.count(1)


def start(kind: str, title: str, *, stage: str = "排队中", link: dict | None = None) -> str:
    """登记一个进行中的任务，返回任务 id。"""
    with _lock:
        task_id = f"{kind}:{next(_seq)}"
        _tasks[task_id] = Task(task_id, kind, title, stage=stage, link=link)
        _prune()
        return task_id


def update(task_id: str | None, *, stage: str | None = None, done: int | None = None,
           total: int | None = None, title: str | None = None) -> None:
    if task_id is None:
        return
    with _lock:
        task = _tasks.get(task_id)
        if task is None or task.status != "running":
            return
        if stage is not None:
            task.stage = stage
        if done is not None:
            task.done = done
        if total is not None:
            task.total = total
        if title is not None:
            task.title = title


def finish(task_id: str | None, status: str = "done", *, detail: str = "", stage: str | None = None,
           link: dict | None = None) -> None:
    if task_id is None:
        return
    with _lock:
        task = _tasks.get(task_id)
        if task is None or task.status != "running":
            return
        task.status, task.detail, task.finished_at = status, detail, time.time()
        if stage is not None:
            task.stage = stage
        if link is not None:
            task.link = link
        _prune()


def tasks() -> list[dict]:
    with _lock:
        return [t.view() for t in _tasks.values()]


def dismiss(task_id: str) -> bool:
    """移除一个已结束的任务；进行中的不能移除（移除了它仍在后台跑，只是看不见）。"""
    with _lock:
        task = _tasks.get(task_id)
        if task is None or task.status == "running":
            return False
        del _tasks[task_id]
        return True


def _prune() -> None:
    finished = sorted((t for t in _tasks.values() if t.status != "running"), key=lambda t: t.finished_at or 0)
    for t in finished[: max(0, len(finished) - MAX_FINISHED)]:
        _tasks.pop(t.id, None)


def reset() -> None:
    """清空（测试用）。"""
    with _lock:
        _tasks.clear()
