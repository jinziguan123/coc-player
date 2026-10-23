"""每条顶层生成路径都要能回答「这次花了多久、花在哪」。

开场是反面教材：玩家第一次等待，却一处埋点都没有。实测一次开场 3 分 42 秒，98% 的输出
token 是模型思考（生成完即丢弃、不进旁白），日志里连总时长都查不到，只能靠体感描述。
"""

import ast
import asyncio
import inspect
import logging
import pathlib

import pytest

from app.services import generation_lifecycle, human_kp_service, turn_orchestrator

API_DIR = pathlib.Path(__file__).resolve().parents[1] / "app" / "api"
#: 打了点就该出现的记号（三者任一即可：统一包装、本模块的薄包装、或路径自带的告警调用）。
_TIMED_MARKERS = ("generation_timing(", "_timed(", "warn_if_reasoning_dominates")


def _started_coroutine_names() -> set[str]:
    """扫 app/api，取出所有交给 generation_manager.start 的协程函数名。

    直接写成 `start(sid, run_x(...))` 的取调用名；先赋给变量再传的（`coro = run_x(...)`）
    回溯同一函数体里的赋值。这样新增一条生成入口就会自动进入本测试的检查范围。
    """
    names: set[str] = set()
    for path in API_DIR.glob("*.py"):
        tree = ast.parse(path.read_text("utf-8"))
        for func in ast.walk(tree):
            if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            assigned: dict[str, set[str]] = {}
            for node in ast.walk(func):
                if not isinstance(node, ast.Assign):
                    continue
                # 三元也要认：开场就是 `opening = run_opening(...) if ai else init_human(...)`
                calls = (
                    [node.value.body, node.value.orelse]
                    if isinstance(node.value, ast.IfExp) else [node.value]
                )
                found = {_callee_name(c) for c in calls if isinstance(c, ast.Call)}
                for target in node.targets:
                    if isinstance(target, ast.Name) and found:
                        assigned.setdefault(target.id, set()).update(found)
            for node in ast.walk(func):
                if not (isinstance(node, ast.Call) and _callee_name(node) == "start"):
                    continue
                for arg in node.args[1:2]:
                    if isinstance(arg, ast.Call):
                        names.add(_callee_name(arg))
                    elif isinstance(arg, ast.Name) and arg.id in assigned:
                        names.update(assigned[arg.id])
    return {n for n in names if n.startswith("run_")}


def _callee_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _resolve(name: str):
    for module in (turn_orchestrator, human_kp_service):
        fn = getattr(module, name, None)
        if fn is not None:
            return fn
    raise AssertionError(f"找不到生成入口 {name}")


def test_扫得到全部生成入口():
    """清单靠扫描得来而非手写，先确认扫描本身没瞎：几条已知路径必须在内。"""
    found = _started_coroutine_names()
    assert {
        "run_chat_generation", "run_opening_generation", "run_roll_generation",
        "run_epilogue_generation", "run_check_request_generation", "run_travel_generation",
        "run_regenerate_generation", "run_human_team_turn_generation",
    } <= found, found


@pytest.mark.parametrize("name", sorted(_started_coroutine_names()))
def test_每条生成入口都打了耗时点(name):
    src = inspect.getsource(_resolve(name))
    assert any(m in src for m in _TIMED_MARKERS), (
        f"{name} 没有耗时打点：玩家等待期间发生了什么将无从查起。"
        f"用 turn_orchestrator._timed(...) 或 generation_lifecycle.generation_timing(...) 包一层。"
    )


def _run(coro):
    return asyncio.run(coro)


def test_合计与思考占比在正常收尾时记一次(caplog):
    async def main():
        async with generation_lifecycle.generation_timing("s1", "开场"):
            pass
    with caplog.at_level(logging.INFO, logger="app.services.generation_lifecycle"):
        _run(main())
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "耗时|开场合计" in text and "s1" in text


def test_异常与取消时同样记合计(caplog):
    """失败那次**更**该知道时间花在哪儿，所以合计写在 finally。"""
    async def boom():
        async with generation_lifecycle.generation_timing("s2", "尾声"):
            raise RuntimeError("上游炸了")
    with caplog.at_level(logging.INFO, logger="app.services.generation_lifecycle"):
        with pytest.raises(RuntimeError):
            _run(boom())
    assert "耗时|尾声合计" in "\n".join(r.getMessage() for r in caplog.records)

    async def cancelled():
        async with generation_lifecycle.generation_timing("s3", "前往"):
            raise asyncio.CancelledError
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="app.services.generation_lifecycle"):
        with pytest.raises(asyncio.CancelledError):
            _run(cancelled())
    assert "耗时|前往合计" in "\n".join(r.getMessage() for r in caplog.records)


def test_排干上轮收尾_慢才记_快不打扰(caplog):
    async def slow_drain(_sid):
        await asyncio.sleep(0.6)

    async def fast_drain(_sid):
        return None

    async def main(drain):
        async with generation_lifecycle.generation_timing("s4", "开场", drain=drain):
            pass

    with caplog.at_level(logging.INFO, logger="app.services.generation_lifecycle"):
        _run(main(slow_drain))
    assert "耗时|开场等上轮收尾" in "\n".join(r.getMessage() for r in caplog.records)

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="app.services.generation_lifecycle"):
        _run(main(fast_drain))
    assert "等上轮收尾" not in "\n".join(r.getMessage() for r in caplog.records)


def test_不传_drain_就不排干():
    """真人 KP 的队友回合不碰 world_state，没必要为它等上一轮的后台收尾。"""
    called = []

    async def drain(sid):
        called.append(sid)

    async def main():
        async with generation_lifecycle.generation_timing("s5", "真人 KP 的队友回合"):
            pass
    _run(main())
    assert called == []


def test_取消排干等待者不吞取消也不中断共享收尾():
    async def run():
        manager = generation_lifecycle.HousekeepingManager()
        release = asyncio.Event()
        task = asyncio.create_task(release.wait())
        manager.tasks["s"] = task
        waiter = asyncio.create_task(manager.drain("s"))
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert not task.done()
        assert manager.tasks["s"] is task
        release.set()
        await manager.drain("s")
        assert "s" not in manager.tasks
    asyncio.run(run())


def test_已失败的收尾会读取异常并清理(caplog):
    async def run():
        async def fail():
            raise ValueError("收尾失败探针")
        manager = generation_lifecycle.HousekeepingManager()
        manager.tasks["s"] = asyncio.create_task(fail())
        await asyncio.sleep(0)
        await manager.drain("s")
        assert "s" not in manager.tasks
    asyncio.run(run())
    assert "后台收尾失败" in caplog.text
