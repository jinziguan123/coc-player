"""确定性效果的执行通道。执行凭据附在效果事件上，与状态原子提交、随重生成一起撤销。

已接入 HP、理智结算、计划物品增减与规划器剧情标志；其它领域效果仍保留原有执行函数，迁移时必须先移除其内部提交。
不跨 LLM 调用持有事务，不靠供应商 tool_call_id 判断语义重复。
"""
from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
from typing import Literal, TypeVar

from sqlalchemy.orm import Session

from app.models.event_log import EventLog

EffectSource = Literal["kp_tool", "kp_text", "planner", "manual", "system"]
T = TypeVar("T")


@dataclass(frozen=True)
class HpChange:
    actor_id: str
    delta: int
    reason: str

    def key(self, anchor: int) -> str:
        # source 不参与键：同一效果通过工具与兼容文本各到达一次，仍只能结算一次。
        payload = [anchor, self.actor_id, self.delta, " ".join(self.reason.split())]
        digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()
        return f"hp_change:{digest}"


@dataclass(frozen=True)
class ItemChange:
    """物品获得/失去。去重沿用既有的 ``turn_state.item_delta_keys`` 台账口径
    （``g|锚点|名字|角色`` / ``l|…``）：物品不随重新生成回滚，而是按键不重复增减。"""
    actor_id: str
    name: str
    qty: int
    op: Literal["gain", "loss"]
    ledger_key: str
    kind: str = ""

    def key(self, anchor: int) -> str:
        return self.ledger_key


@dataclass(frozen=True)
class FlagChange:
    """一次剧情推进：若干标志的置/清与其 KP 专属记录同提交。幂等由白名单校验保证
    （已激活不再置、未激活不清），这里不另设台账。"""
    set_flags: tuple[str, ...]
    clear_flags: tuple[str, ...]

    def key(self, anchor: int) -> str:
        return f"flags:{anchor}:{','.join(sorted(self.set_flags))}|{','.join(sorted(self.clear_flags))}"


@dataclass(frozen=True)
class SanChange:
    """一次理智检定结算。去重仍由调用方的恐怖源台账（``session_ledger.san_checked``）与
    本轮结算簿（``turn_state.san_turn``）负责；通道只保证 SAN、疯狂状态、骰子事件与台账同提交。"""
    actor_id: str
    source: str

    def key(self, anchor: int) -> str:
        return f"san:{self.source}|{self.actor_id}"


@dataclass(frozen=True)
class EffectProposal:
    payload: HpChange | ItemChange | FlagChange | SanChange
    source: EffectSource
    anchor: int

    def receipt(self) -> dict:
        if isinstance(self.payload, HpChange):
            kind = "hp_change"
        elif isinstance(self.payload, ItemChange):
            kind = f"item_{self.payload.op}"
        elif isinstance(self.payload, SanChange):
            kind = "san_check"
        else:
            kind = "plot_flags"
        return {"key": self.payload.key(self.anchor), "kind": kind,
                "source": self.source, "anchor": self.anchor}


def execute(
    db: Session, session_id: str, proposal: EffectProposal,
    apply: Callable[[dict], T], *, duplicate: T,
    already: Callable[[], bool] | None = None, commit: bool = True,
) -> T:
    """apply 只 flush，并将 receipt 写到效果事件；本函数拥有提交/回滚权。

    ``already`` 给定时用它判重（物品用 turn_state 台账），否则按效果事件上的凭据判重。
    ``commit=False`` 时仅暂存，由调用方的事务统一提交；不得单独使用而遗漏提交。
    """
    receipt = proposal.receipt()
    try:
        if already is not None:
            if already():
                return duplicate
        elif proposal.source != "manual":
            rows = db.query(EventLog).filter(
                EventLog.session_id == session_id,
                EventLog.sequence_num > proposal.anchor,
                EventLog.event_type == "system",
            ).all()
            if any((ev.metadata_ or {}).get("effect", {}).get("key") == receipt["key"] for ev in rows):
                return duplicate
        result = apply(receipt)
        if commit:
            db.commit()
        else:
            db.flush()
        return result
    except BaseException:
        db.rollback()
        raise


@contextmanager
def transaction(db: Session):
    """不经提案去重、只需「要么都落、要么都不落」的确定性写入（如投骰事件 + 消费待投卡）。"""
    try:
        yield
        db.commit()
    except BaseException:
        db.rollback()
        raise


@dataclass(frozen=True)
class EffectAuthority:
    """一类效果的权威登记：谁能提、按什么去重、重新生成时怎么处理。

    这是草案「权威表」的落地形态：新增效果类型必须在这里登记一行（测试会检查），
    而不是在各入口里各补一个守卫。冲突裁决仍由各执行函数完成，这里只做可查的声明。
    """
    kind: str
    sources: tuple[str, ...]
    dedupe: str
    on_regenerate: str


AUTHORITY: dict[type, EffectAuthority] = {
    HpChange: EffectAuthority(
        "hp_change", ("kp_tool", "kp_text", "planner", "manual"),
        "自动来源按「回合锚点+角色+数值+规范化理由」去重（凭据在 HP 事件 metadata.effect）；真人 KP 不去重",
        "按撤销台账回滚（随事件删除），重生成可重新结算",
    ),
    ItemChange: EffectAuthority(
        "item_gain/item_loss", ("planner",),
        "turn_state.item_delta_keys 台账键（g|锚点|名字|角色 / l|…）；门控收获先暂存，检定落地再兑现",
        "不回滚；按台账键不重复增减",
    ),
    FlagChange: EffectAuthority(
        "plot_flags", ("planner",),
        "模组白名单 + 已激活不再置/未激活不清（KP 工具与文本的单个标志另走 model_flag_rejection）",
        "不回滚；再次推进由白名单自然幂等",
    ),
    SanChange: EffectAuthority(
        "san_check", ("system", "manual"),
        "session_ledger.san_checked「恐怖源|角色」+ 本轮 san_turn 结算簿；玩家投骰时消费待投卡同事务",
        "不回滚；已结算转 credit，重生成的同角色 SAN 先抵扣",
    ),
}
