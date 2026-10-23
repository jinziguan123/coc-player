"""共用流水线的行为契约：事件顺序、失败持久化与缓冲丢弃。"""
import asyncio

import pytest

from app.services.event_protocol import make_chunk
from app.services.turn_pipeline import NarrationResult, TurnContext, TurnSpec, run


@pytest.mark.parametrize("failure", [None, "stream", "validate", "cancel"])
def test_流水线正常与失败路径只持久化一次(failure):
    result = NarrationResult()
    ctx = TurnContext("s", result, TurnSpec("测试"))
    calls = []

    async def stream():
        result.narration = "已生成"
        yield make_chunk("narration", result.narration)
        if failure == "stream":
            raise ValueError("断流")
        if failure == "cancel":
            raise asyncio.CancelledError

    async def validate():
        calls.append("validate")
        if failure == "validate":
            raise ValueError("校验失败")
        result.narration = "校验后"

    async def execute():
        await run(ctx, stream(), broadcast=lambda chunk: calls.append(chunk.content),
                  validate=validate, persist=lambda: calls.append(("persist", result.narration)))

    if failure:
        with pytest.raises(asyncio.CancelledError if failure == "cancel" else ValueError):
            asyncio.run(execute())
    else:
        asyncio.run(execute())
    persisted = [c for c in calls if isinstance(c, tuple)]
    assert persisted == [("persist", "已生成" if failure else "校验后")]
    assert calls[0] == "已生成"


def test_检定缓冲失败不把未展示的推测保存到历史():
    result = NarrationResult()
    ctx = TurnContext("s", result, TurnSpec("检定", hold_for_check=True))
    shown, saved = [], []

    async def stream():
        result.narration = "提前泄露的秘密"
        result.raw = "[DICE_CHECK: skill=侦查]"
        yield make_chunk("narration", result.narration)
        raise asyncio.CancelledError

    async def validate():
        pass

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run(ctx, stream(), broadcast=shown.append, validate=validate,
                        persist=lambda: saved.append(result.narration)))
    assert shown == [] and saved == [""]
    assert result.raw.startswith("[DICE_CHECK")


def test_拒绝长检定旁白仍保留已结算工具卡():
    result = NarrationResult(narration="不应出现的长旁白")
    ctx = TurnContext("s", result, TurnSpec("检定", hold_for_check=True))
    shown = []

    async def stream():
        yield make_chunk("narration", result.narration)
        yield make_chunk("check_request", "请投骰")

    async def validate():
        assert not result.narration

    asyncio.run(run(ctx, stream(), broadcast=shown.append, validate=validate,
                    persist=lambda: None, keep=lambda _: False))
    assert [e.type for e in shown] == ["check_request"]


@pytest.mark.parametrize("failure", ["stream", "cancel"])
def test_部分产物保存失败不掩盖原始异常(failure):
    result = NarrationResult()
    ctx = TurnContext("s", result, TurnSpec("测试"))
    attempts = []

    async def stream():
        yield make_chunk("narration", "半截")
        raise asyncio.CancelledError if failure == "cancel" else ValueError("断流")

    async def validate():
        pass

    def persist():
        attempts.append(1)
        raise RuntimeError("磁盘满")

    with pytest.raises(asyncio.CancelledError if failure == "cancel" else ValueError):
        asyncio.run(run(ctx, stream(), broadcast=lambda c: None, validate=validate, persist=persist))
    assert attempts == [1] and ctx.stage == "persist_partial"


def test_正常落库失败只尝试一次并上抛():
    result = NarrationResult()
    ctx = TurnContext("s", result, TurnSpec("测试"))
    attempts = []

    async def stream():
        yield make_chunk("narration", "完整")

    async def validate():
        pass

    def persist():
        attempts.append(1)
        raise RuntimeError("提交失败")

    with pytest.raises(RuntimeError, match="提交失败"):
        asyncio.run(run(ctx, stream(), broadcast=lambda c: None, validate=validate, persist=persist))
    assert attempts == [1]


def test_取消时关闭内层生成器():
    closed = []
    result = NarrationResult()
    ctx = TurnContext("s", result, TurnSpec("测试"))

    async def inner():
        try:
            yield make_chunk("narration", "一")
            await asyncio.sleep(10)
            yield make_chunk("narration", "二")
        finally:
            closed.append(True)

    async def main():
        task = asyncio.create_task(run(
            ctx, inner(), broadcast=lambda c: None, validate=lambda: asyncio.sleep(0),
            persist=lambda: None,
        ))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(main())
    assert closed == [True]


def test_narration_result_兼容旧位置接口():
    r = NarrationResult()
    r[0] += "旁白"
    r[1] = "[SET_FLAG: x]"
    r[2].append(("NPC", "台词"))
    r[3].append((2, "NPC", "台词"))
    assert len(r) == 5 and r[-1] is r.group_marks
    assert r[3:] == [r.dialogue_marks, r.group_marks]
    narration, raw, bubbles, marks, groups = r
    assert (narration, raw, bubbles, marks, groups) == ("旁白", "[SET_FLAG: x]", [("NPC", "台词")], [(2, "NPC", "台词")], [])
    assert list(r)[0] == "旁白"
    with pytest.raises(IndexError):
        r[5]


# ── 高风险缓冲：终检完成才放出 ───────────────────────────────────────────


def _buffered_case(validate_impl, *, timeout=None, on_failure="release"):
    from app.services.turn_pipeline import replay_held  # noqa: F401
    result = NarrationResult()
    ctx = TurnContext("s", result, TurnSpec(
        "高风险", buffer_until_validated=True, validate_timeout=timeout,
        on_validation_failure=on_failure,
    ))
    shown, saved, timeline = [], [], []

    async def stream():
        result.narration = "门后是"
        yield make_chunk("narration", "门后是")
        result.dialogue_marks.append((3, "管家", "别进去"))
        result.bubbles.append(("管家", "别进去"))
        yield make_chunk("dialogue", "别进去", actor_name="管家")
        yield make_chunk("dice", "侦查 成功")
        result.narration += "老爷的尸体。"
        yield make_chunk("narration", "老爷的尸体。")

    async def validate():
        timeline.append(("validate", len(shown)))
        await validate_impl(result, ctx)

    def broadcast(chunk):
        shown.append((chunk.type, chunk.content))

    def persist():
        saved.append(result.narration)

    return ctx, stream, validate, broadcast, persist, shown, saved, timeline


def test_高风险回合终检通过前不放出任何内容且顺序不变():
    async def passed(result, ctx):
        ctx.validation["status"] = "pass"

    ctx, stream, validate, broadcast, persist, shown, saved, timeline = _buffered_case(passed)
    asyncio.run(run(ctx, stream(), broadcast=broadcast, validate=validate, persist=persist))
    assert timeline == [("validate", 0)]           # 校验时尚未展示任何东西
    assert shown == [("narration", "门后是"), ("dialogue", "别进去"), ("dice", "侦查 成功"),
                     ("narration", "老爷的尸体。")]
    assert saved == ["门后是老爷的尸体。"]


def test_终检改写后按原交错顺序放出改写稿():
    async def rewrite(result, ctx):
        result.narration = "门后是一片黑暗。"
        ctx.validation["status"] = "rewritten"

    ctx, stream, validate, broadcast, persist, shown, saved, _ = _buffered_case(rewrite)
    asyncio.run(run(ctx, stream(), broadcast=broadcast, validate=validate, persist=persist))
    types = [t for t, _ in shown]
    assert types.index("dialogue") < types.index("dice")
    assert types[0] == "narration" and types[-1] == "narration"   # 台词与骰子仍夹在旁白之间
    assert "".join(c for t, c in shown if t == "narration") == "门后是一片黑暗。"
    assert "尸体" not in "".join(c for _, c in shown)
    assert saved == ["门后是一片黑暗。"]


@pytest.mark.parametrize("on_failure", ["release", "withhold"])
def test_终检超时按配置放行或暂扣(on_failure):
    async def slow(result, ctx):
        result.narration = "半途改写"   # 超时被取消，部分修改必须回滚
        await asyncio.sleep(10)

    ctx, stream, validate, broadcast, persist, shown, saved, _ = _buffered_case(
        slow, timeout=0.01, on_failure=on_failure,
    )
    asyncio.run(run(ctx, stream(), broadcast=broadcast, validate=validate, persist=persist))
    assert ctx.validation["status"] == "timeout"
    narration = "".join(c for t, c in shown if t == "narration")
    if on_failure == "release":
        assert narration == "门后是老爷的尸体。" and saved == ["门后是老爷的尸体。"]
        assert ("dialogue", "别进去") in shown
    else:
        from app.services.turn_pipeline import WITHHELD_NARRATION
        assert "尸体" not in narration and narration == WITHHELD_NARRATION
        assert ("dice", "侦查 成功") in shown and ("dialogue", "别进去") not in shown
        assert saved == [WITHHELD_NARRATION]


def test_终检不可用视同失败():
    async def unavailable(result, ctx):
        ctx.validation["status"] = "unavailable"

    ctx, stream, validate, broadcast, persist, shown, saved, _ = _buffered_case(
        unavailable, on_failure="withhold",
    )
    asyncio.run(run(ctx, stream(), broadcast=broadcast, validate=validate, persist=persist))
    assert "尸体" not in "".join(c for _, c in shown)


def test_缓冲期间取消不展示也不落库未校验旁白():
    async def hang(result, ctx):
        await asyncio.sleep(10)

    ctx, stream, validate, broadcast, persist, shown, saved, _ = _buffered_case(hang)

    async def main():
        task = asyncio.create_task(run(ctx, stream(), broadcast=broadcast, validate=validate, persist=persist))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(main())
    assert shown == [] and saved == [""]


# ── 后置效果：统一顺序 + 入口声明 ─────────────────────────────────────────


def _record_post_effects(
    monkeypatch, allowed, *, plan=None, plan_factory=None, hung=False, requested=None,
):
    from types import SimpleNamespace

    from app.services import combat_service, plot_progress, planned_effects, turn_context, turn_orchestrator
    calls = []

    def gen(name):
        async def guard(*a, **k):
            calls.append(name)
            if False:
                yield None
        return guard

    mapping = {
        "_ensure_planned_combat": "planned_combat", "_ensure_narrated_combat": "narrated_combat",
        "_ensure_planned_scene": "scene", "_ensure_scene_entry_checks": "entry_checks",
        "_ensure_planned_sanity": "sanity", "_ensure_planned_mishap": "mishap",
        "_ensure_planned_items": "items", "_ensure_planned_combat_damage": "combat_damage",
    }
    for attr, name in mapping.items():
        monkeypatch.setattr(turn_orchestrator, attr, gen(name))
    monkeypatch.setattr(plot_progress, "apply_planned_flags", gen("flags"))
    monkeypatch.setattr(planned_effects, "record_narrated_progress", lambda *a, **k: calls.append("narrated_progress"))
    monkeypatch.setattr(
        turn_context, "checks_hung_since", hung if callable(hung) else (lambda *a, **k: hung),
    )
    monkeypatch.setattr(combat_service, "get_combat", lambda s: None)

    class _DB:
        def refresh(self, obj):
            pass

    asyncio.run(turn_orchestrator._run_post_effects(
        _DB(), "s", SimpleNamespace(), None, None, [], None,
        allowed=allowed, plan=plan, pre_gen_seq=0, combat_before=False, plan_factory=plan_factory,
        requested_check=(lambda: requested(calls)) if requested else None,
    ))
    return calls


def test_常规回合按规范顺序执行全部允许的后置效果(monkeypatch):
    from app.services.turn_pipeline import ENTRY_EFFECTS, POST_EFFECT_ORDER
    calls = _record_post_effects(monkeypatch, ENTRY_EFFECTS["regular"])
    assert calls == [e for e in POST_EFFECT_ORDER if e not in ("mishap", "requested_check")]


def test_旁路续写只执行声明的效果且规划在依赖计划的效果之前现跑(monkeypatch):
    from app.ai.turn_planner import TurnPlan
    from app.services.turn_pipeline import ENTRY_EFFECTS
    seen = []

    async def factory():
        seen.append("planner")
        return TurnPlan()

    calls = _record_post_effects(monkeypatch, ENTRY_EFFECTS["side"], plan_factory=factory)
    assert calls == ["narrated_combat", "entry_checks", "sanity", "mishap", "flags", "narrated_progress"]
    assert seen == ["planner"]
    assert not {"scene", "items", "planned_combat", "combat_damage"} & set(calls)


def test_挂起检定且计划要求检定时跳过场景_进场与SAN(monkeypatch):
    from app.ai.turn_planner import TurnPlan
    from app.services.turn_pipeline import ENTRY_EFFECTS
    calls = _record_post_effects(
        monkeypatch, ENTRY_EFFECTS["regular"], plan=TurnPlan(requires_check=True), hung=True,
    )
    assert not {"scene", "entry_checks", "sanity"} & set(calls)
    assert "items" in calls and "flags" in calls   # 物品走暂存、标志照常校验


def test_申请检定兜底先于挂起判定_补挂后进场检定让路(monkeypatch):
    from app.services.turn_pipeline import TurnSpec
    state = {"hung": False}

    async def fallback(calls):
        calls.append("requested_check")
        state["hung"] = True   # 兜底补挂了检定
        if False:
            yield None

    calls = _record_post_effects(
        monkeypatch, TurnSpec.for_entry("side").effects, requested=fallback,
        hung=lambda *a, **k: state["hung"],
    )
    assert calls[:2] == ["narrated_combat", "requested_check"]
    assert "entry_checks" not in calls


def test_入口效果声明经_TurnSpec_取得():
    from app.services.turn_pipeline import ENTRY_EFFECTS, TurnSpec
    assert TurnSpec.for_entry("side").effects == ENTRY_EFFECTS["side"]
    assert "requested_check" not in TurnSpec.for_entry("regular").effects


# ── stream_stage：生成器出口（文本路径续写/查阅续写）──────────────────────


def _stage_ctx():
    from app.services.turn_pipeline import TurnContext as Ctx
    r = NarrationResult()
    return r, Ctx("s", r, TurnSpec("续写"))


def test_stream_stage_正常结束先校验再落库一次():
    from app.services.turn_pipeline import stream_stage
    r, ctx = _stage_ctx()
    log = []

    async def src():
        r.narration = "续写"
        yield make_chunk("narration", "续写")

    async def validate():
        log.append("validate")

    async def main():
        return [c.content async for c in stream_stage(
            ctx, src(), validate=validate, persist=lambda: log.append(("persist", r.narration)),
        )]

    assert asyncio.run(main()) == ["续写"]
    assert log == ["validate", ("persist", "续写")]


def test_stream_stage_调用方中途关闭也保存半截且只保存一次():
    from app.services.turn_pipeline import stream_stage
    r, ctx = _stage_ctx()
    saved = []

    async def src():
        r.narration = "前半"
        yield make_chunk("narration", "前半")
        r.narration += "后半"
        yield make_chunk("narration", "后半")

    async def validate():
        raise AssertionError("中断后不应校验")

    async def main():
        gen = stream_stage(ctx, src(), validate=validate, persist=lambda: saved.append(r.narration))
        await gen.__anext__()
        await gen.aclose()

    asyncio.run(main())
    assert saved == ["前半"] and ctx.stage == "persist_partial"


def test_stream_stage_保存失败不掩盖断流异常():
    from app.services.turn_pipeline import stream_stage
    r, ctx = _stage_ctx()

    async def src():
        yield make_chunk("narration", "一")
        raise ValueError("断流")

    def persist():
        raise RuntimeError("磁盘满")

    async def main():
        async for _ in stream_stage(ctx, src(), validate=lambda: asyncio.sleep(0), persist=persist):
            pass

    with pytest.raises(ValueError, match="断流"):
        asyncio.run(main())
