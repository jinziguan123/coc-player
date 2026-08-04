"""后台收尾（滚动摘要 + 幕后推演）必须走快模型。

它们是结构化副任务——浓缩既往事件、按 NPC 动机推演，不吃文笔，正是快模型这档的既定职责
（设置页写的就是「裁定 planner、AI 队友、滚动摘要走它」）。

曾经误传主模型：主模型往往开着思考换文笔，实测收尾因此要 30.9s。而这 30.9s 不只是后台慢——
下一回合开头与投骰后的 KP 续写都要 drain 完它才能动 world_state，于是直接变成玩家的等待。
"""

from app.services import turn_orchestrator


def test_housekeeping_uses_fast_model(monkeypatch):
    picked = {}
    monkeypatch.setattr(turn_orchestrator, "get_fast_llm", lambda: "FAST")
    monkeypatch.setattr(turn_orchestrator, "get_llm", lambda: "MAIN")
    monkeypatch.setattr(
        turn_orchestrator._housekeeping_manager, "spawn",
        lambda session_id, llm, *tasks: picked.update(llm=llm),
    )

    turn_orchestrator._spawn_housekeeping("s1")

    assert picked["llm"] == "FAST", "收尾走主模型会把它的思考耗时转嫁成玩家的等待"


def test_finish_generation_ignores_the_narration_llm(monkeypatch):
    """收尾自取快模型，与本次叙事用的 llm 无关——三处调用点都传了主模型，
    在这里统一取才不会漏。"""
    picked = {}
    monkeypatch.setattr(turn_orchestrator, "get_fast_llm", lambda: "FAST")
    monkeypatch.setattr(
        turn_orchestrator._housekeeping_manager, "spawn",
        lambda session_id, llm, *tasks: picked.update(llm=llm),
    )
    monkeypatch.setattr(turn_orchestrator.room_hub, "broadcast", lambda *a, **k: None)

    import asyncio
    asyncio.run(turn_orchestrator._finish_generation(None, "s1", "MAIN_NARRATION_LLM"))

    assert picked["llm"] == "FAST"
