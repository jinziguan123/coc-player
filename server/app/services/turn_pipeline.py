"""各生成入口共用的叙事生命周期：生成、校验、落库与取消收尾。

领域效果与上下文选择仍由入口决定；流水线只管理产物和阶段，不调用模型或改游戏状态。
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
import logging
from typing import Any, ClassVar

logger = logging.getLogger(__name__)


@dataclass
class NarrationResult:
    narration: str = ""
    raw: str = ""
    bubbles: list[tuple[str, str]] = field(default_factory=list)
    dialogue_marks: list[tuple[int, str, str]] = field(default_factory=list)
    group_marks: list[tuple[int, str]] = field(default_factory=list)

    # 兼容已有解析器/插件的位置接口；新编排代码用命名字段，不再创建裸列表。
    _fields: ClassVar[tuple[str, ...]] = (
        "narration", "raw", "bubbles", "dialogue_marks", "group_marks",
    )

    def __len__(self):
        return len(self._fields)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [getattr(self, name) for name in self._fields[index]]
        return getattr(self, self._fields[index])

    def __setitem__(self, index: int, value):
        setattr(self, self._fields[index], value)

    def clear_presentation(self) -> None:
        """抛弃未展示的叙事，保留指令原文供确定性处理。"""
        self.narration = ""
        self.bubbles.clear()
        self.dialogue_marks.clear()
        self.group_marks.clear()


#: 后置效果的规范顺序。所有入口共用这一顺序，入口只能声明「允许哪些」，不能自定先后：
#: 开战先于场景（打起来了就不再搬人）；场景先于进场检定与 SAN（进了新场景才读得到它的机制）；
#: 剧情标志与叙事记账殿后（本轮收尾的幕后推演以新进度为准）。
POST_EFFECT_ORDER: tuple[str, ...] = (
    "planned_combat", "narrated_combat", "requested_check", "scene", "entry_checks", "sanity",
    "mishap", "items", "combat_damage", "flags", "narrated_progress",
)

#: 各入口允许落地的后置效果。偏离「常规回合」的每一项都写明理由；没有理由的偏离就是 bug。
ENTRY_EFFECTS: dict[str, frozenset[str]] = {
    # 常规回合 / 重新生成 / 开场：回合起点已有（或现跑）完整计划。
    # 反噬只在「叙事后才知道掷出了大失败」的投骰续写里判，常规回合的大失败由 KP 当场演；
    # 玩家申请检定的兜底只属于检定申请入口。
    "regular": frozenset(POST_EFFECT_ORDER) - {"mishap", "requested_check"},
    # 旁路续写（投骰后、检定申请、前往、战斗余波、幸运决策）：回合起点没有计划——
    # 不执行计划开战/搬场景/发物品/范围伤害（无从裁定）；SAN、反噬、标志只取叙事后现跑的规划。
    # requested_check 排在挂起判定之前：兜底补挂的检定同样让后面的守卫让路。
    "side": frozenset({
        "narrated_combat", "requested_check", "entry_checks", "sanity", "mishap", "flags",
        "narrated_progress",
    }),
    # 规划器识别出玩家申请检定：叙事走旁路的检定裁定（_run_kp_turn），后置效果同 side。
    "check_request": frozenset({
        "narrated_combat", "requested_check", "entry_checks", "sanity", "mishap", "flags",
        "narrated_progress",
    }),
}


#: 叙事前效果：在 KP 动笔前就要定下、且不依赖本轮叙事的确定性效果。
#: gated_items：被本轮检定门控的收获先暂存（失去/消耗是尝试本身的代价，照常结算），骰子落地再兑现。
#: moves：规划器的移动提案先裁定——无需检定的立刻落位（叙事据新位置生成），需检定的暂存到骰子落地。
#: 常规回合的移动裁定在 ``_run_generation`` 里叙事前执行（那里才有现跑的计划），不经这张表。
PRE_EFFECT_ORDER: tuple[str, ...] = ("gated_items", "moves")

#: 各入口允许的叙事前效果。常规回合的物品在叙事后按计划结算（见 POST_EFFECT_ORDER 的 items），
#: 只有「规划器识别出玩家申请检定」的分支会把整份计划交给检定裁定，需要先把门控收获暂存。
PRE_ENTRY_EFFECTS: dict[str, frozenset[str]] = {
    "regular": frozenset(),
    "side": frozenset(),
    "check_request": frozenset({"gated_items", "moves"}),
}

#: 高风险缓冲在校验不可用/超时、且策略为 withhold 时展示与落库的占位旁白。
WITHHELD_NARRATION = "（守秘人斟酌片刻，这段叙述未能通过保密校验，暂不展示。可点「重新生成」再试。）"
_VALIDATION_FAILED = {"unavailable", "rewrite_missing", "timeout", "error"}


@dataclass(frozen=True)
class TurnSpec:
    name: str
    hold_for_check: bool = False
    # 高风险回合（如 do_not_reveal 非空）：整段缓冲到终检完成后再按原交错顺序放出。
    buffer_until_validated: bool = False
    validate_timeout: float | None = None
    on_validation_failure: str = "release"   # release=放出原文（不保证不泄密）| withhold=占位
    # 本入口叙事之后允许落地的后置效果（取自 ENTRY_EFFECTS，顺序恒为 POST_EFFECT_ORDER）
    effects: frozenset[str] = frozenset()
    # 本入口叙事之前允许落地的效果（取自 PRE_ENTRY_EFFECTS，顺序恒为 PRE_EFFECT_ORDER）
    pre_effects: frozenset[str] = frozenset()

    @classmethod
    def for_entry(cls, entry: str, **kw) -> "TurnSpec":
        return cls(
            entry, effects=ENTRY_EFFECTS.get(entry, frozenset()),
            pre_effects=PRE_ENTRY_EFFECTS.get(entry, frozenset()), **kw,
        )


@dataclass
class TurnContext:
    session_id: str
    result: NarrationResult
    spec: TurnSpec
    pre_gen_seq: int = 0
    event_order: list[tuple[int, str]] = field(default_factory=list)
    group: str | None = None
    stage: str = "prepare"
    validation: dict = field(default_factory=dict)
    released: bool = False


def _narration_chunk(template: Any, text: str) -> Any:
    if template is not None:
        return template.model_copy(update={"content": text})
    from app.services.event_protocol import make_chunk
    return make_chunk("narration", text)


def replay_held(held: list, new_text: str | None) -> list:
    """按缓冲时的交错顺序重放；旁白被改写时按长度比例把新文本切回各非旁白事件之间。

    不能整段替换后追加——那会把台词气泡与工具卡全挤到旁白之后，造成错序。
    """
    old_len = sum(len(getattr(c, "content", "") or "") for c in held if getattr(c, "type", "") == "narration")
    old_text = "".join(getattr(c, "content", "") or "" for c in held if getattr(c, "type", "") == "narration")
    if new_text is None or new_text == old_text:
        return list(held)
    ratio = len(new_text) / old_len if old_len else 0.0
    out: list = []
    emitted_old = emitted_new = 0
    template = None
    last_template = None
    for chunk in held:
        if getattr(chunk, "type", "") == "narration":
            emitted_old += len(getattr(chunk, "content", "") or "")
            template = template or chunk
            last_template = chunk
            continue
        cut = min(len(new_text), round(emitted_old * ratio))
        if cut > emitted_new:
            out.append(_narration_chunk(template, new_text[emitted_new:cut]))
            emitted_new = cut
        template = None
        out.append(chunk)
    if emitted_new < len(new_text):
        out.append(_narration_chunk(template or last_template, new_text[emitted_new:]))
    return out


async def run(
    ctx: TurnContext, stream: AsyncIterator[Any], *,
    broadcast: Callable[[Any], None],
    validate: Callable[[], Awaitable[None]],
    persist: Callable[[], None],
    after_generate: Callable[[], None] | None = None,
    keep: Callable[[NarrationResult], bool] | None = None,
) -> None:
    """取消/断流/校验失败均保存已生成内容；持久化失败不重复尝试提交。

    缓冲模式下未放出的旁白在取消/出错时不落库（与检定缓冲同理），只有放出的版本才进存档。
    """
    spec = ctx.spec
    buffered = spec.hold_for_check or spec.buffer_until_validated
    held = []
    ctx.stage = "generate"
    try:
        try:
            async for chunk in stream:
                if ctx.group:
                    meta = dict(getattr(chunk, "metadata", None) or {})
                    meta["group"] = ctx.group
                    chunk = chunk.model_copy(update={"metadata": meta})
                if buffered:
                    held.append(chunk)
                else:
                    broadcast(chunk)
        finally:
            # 关闭外层生成器也会将取消传给内部 NPC 守卫及工具循环。
            close = getattr(stream, "aclose", None)
            if close is not None:
                await close()
        if spec.hold_for_check:
            retained = keep is None or keep(ctx.result)
            if not retained:
                ctx.result.clear_presentation()
                ctx.event_order.clear()
                held = [c for c in held if getattr(c, "type", None) not in {"narration", "dialogue"}]
        if not spec.buffer_until_validated:
            for chunk in held:
                broadcast(chunk)
            ctx.released = True
        if after_generate is not None:
            after_generate()
        ctx.stage = "validate"
        if spec.buffer_until_validated:
            await _validate_buffered(ctx, held, validate, broadcast)
        else:
            await validate()
    except BaseException:
        # 未放出的旁白（检定缓冲 / 高风险缓冲）不能在重连时从存档漏出来。
        if buffered and not ctx.released:
            ctx.result.clear_presentation()
            ctx.event_order.clear()
        ctx.stage = "persist_partial"
        try:
            persist()
        except Exception:
            # 半截产物保存失败只记日志：向上抛的必须是最初的取消/断流/校验异常。
            logger.exception("叙事部分产物保存失败: session=%s", ctx.session_id)
        raise
    ctx.stage = "persist"
    persist()  # 失败直接上抛，由入口统一落错误提示；不在此重试，避免重复提交旁白。
    ctx.stage = "complete"


async def _validate_buffered(ctx: TurnContext, held: list, validate, broadcast) -> None:
    """缓冲回合：终检完成才放出；失败/超时按策略放行原文或以占位替代。"""
    import asyncio
    import copy

    r = ctx.result
    snapshot = (r.narration, copy.deepcopy(r.bubbles), copy.deepcopy(r.dialogue_marks),
                copy.deepcopy(r.group_marks), list(ctx.event_order))
    status = ""
    try:
        if ctx.spec.validate_timeout:
            await asyncio.wait_for(validate(), ctx.spec.validate_timeout)
        else:
            await validate()
        status = ctx.validation.get("status") or "skipped"
    except asyncio.TimeoutError:
        status = "timeout"
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("高风险叙事终检异常: session=%s", ctx.session_id)
        status = "error"
    ctx.validation["status"] = status
    if status in _VALIDATION_FAILED:
        # 校验没做成：回到原文，再按策略决定放不放（放行不保证不泄密，日志明确记录）。
        r.narration, r.bubbles, r.dialogue_marks, r.group_marks, order = snapshot
        ctx.event_order[:] = order
        if ctx.spec.on_validation_failure == "withhold":
            logger.warning("高风险叙事终检%s，按配置暂扣旁白: session=%s", status, ctx.session_id)
            r.clear_presentation()
            r.narration = WITHHELD_NARRATION
            ctx.event_order.clear()
            kept = [c for c in held if getattr(c, "type", None) not in {"narration", "dialogue"}]
            template = next((c for c in held if getattr(c, "type", None) == "narration"), None)
            held = kept + [_narration_chunk(template, WITHHELD_NARRATION)]
        else:
            logger.warning(
                "高风险叙事终检%s，按配置放行未经校验的原文（不保证不泄密）: session=%s",
                status, ctx.session_id,
            )
        for chunk in held:
            broadcast(chunk)
    else:
        for chunk in replay_held(held, r.narration):
            broadcast(chunk)
    ctx.released = True


async def stream_stage(
    ctx: TurnContext, stream: AsyncIterator[Any], *,
    validate: Callable[[], Awaitable[None]],
    persist: Callable[[], None],
) -> AsyncIterator[Any]:
    """``run`` 的生成器版：给本身也是生成器的出口（文本路径的投骰续写、规则/原文/回忆查阅续写）。

    与 ``run`` 同一套失败语义：取消、断流、调用方关闭生成器或校验失败都先保存已生成部分
    （保存再失败只记日志，向上抛原始异常）；正常结束先校验再落库一次。不支持缓冲模式。
    """
    ctx.stage = "generate"
    try:
        try:
            async for chunk in stream:
                if ctx.group:
                    meta = dict(getattr(chunk, "metadata", None) or {})
                    meta["group"] = ctx.group
                    chunk = chunk.model_copy(update={"metadata": meta})
                yield chunk
        finally:
            close = getattr(stream, "aclose", None)
            if close is not None:
                await close()
        ctx.stage = "validate"
        await validate()
    except BaseException:
        ctx.stage = "persist_partial"
        try:
            persist()
        except Exception:
            logger.exception("续写部分产物保存失败: session=%s", ctx.session_id)
        raise
    ctx.stage = "persist"
    persist()
    ctx.stage = "complete"
