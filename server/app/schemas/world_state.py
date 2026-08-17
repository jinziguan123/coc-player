"""world_state 剧情记忆的 Pydantic schema（拆表后残留 JSON 的稳定键定型）。

拆表后 world_state 收敛为「剧情记忆」容器，本模块给它建 schema + 版本边界（ADR-003 决策
第 2、3、5 条）。只给稳定键定型，其余仍在 world_state 里的键（回合级 pending_*、分头
party_locations、幂等台账 san_checked/scene_events_seen、模组设定缓存等）一律 extra="allow"
放行——它们要么已有各自语义、要么会在后续轮次继续拆出去。

校验是 **fail-open** 的：world_state 是跑团剧本的柔性容器，写坏一个字段不该让整局崩掉，
所以 services/world_state.py 的 set_key/mutate 里校验失败只告警、不阻断。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class WorldStateSchema(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: int = 1

    # —— 剧情记忆（保留在 JSON 的稳定键）——
    flags: dict = Field(default_factory=dict)               # 剧情 flags
    clue_ledger: dict = Field(default_factory=dict)         # 线索台账
    npc_memory: dict = Field(default_factory=dict)          # NPC 记忆
    team_memory: dict = Field(default_factory=dict)         # 队伍记忆
    backstage: dict = Field(default_factory=dict)           # 幕后游标
    improvised_npcs: list = Field(default_factory=list)     # 临场 NPC 卡
    story_summary: str = ""                                  # 滚动剧情摘要
    story_summary_seq: int = 0                              # 摘要游标（已浓缩到的 seq）
    handouts_issued: list = Field(default_factory=list)     # 已发放 handouts
    ending_reached: bool = False
    epilogue_done: bool = False
    end_vote: dict = Field(default_factory=dict)            # 结束投票
    budget_scale: float = 1.0                                # 上下文预算校准系数
