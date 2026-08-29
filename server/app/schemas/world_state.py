"""world_state 剧情记忆的 Pydantic schema（拆表后残留 JSON 的稳定键定型）。

拆表后 world_state 收敛为「剧情记忆」容器，本模块给它建 schema + 版本边界（ADR-003 决策
第 2、3、5 条）。回合级 pending_* 与幂等台账已分别搬去 ``turn_state`` 与 ``session_ledger``，
导航去了 ``session_navigation``；此处覆盖的是**仍留在 JSON 里的全部键**。

``extra="allow"`` 仍开着（模组设定缓存等随剧本流动的东西照旧放行），但**不该再靠它承载
已知的键**——声明出来才有人替你检查形状。曾经 ``improvised_npcs`` 被误声明成 list、
运行时全是 dict，正因为校验 fail-open + 没人比对，这个错一直没被照出来。

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
    # 临场 NPC：**按名字建键的 dict**（world_memory.register_improvised_npc /
    # promote_service 都按 improvised_npcs[name] 读写）。曾误声明为 list——因为本 schema
    # 的校验是 fail-open（只告警不阻断），这个错声明一直没被任何用例照出来。
    improvised_npcs: dict = Field(default_factory=dict)
    story_summary: str = ""                                  # 滚动剧情摘要
    story_summary_seq: int = 0                              # 摘要游标（已浓缩到的 seq）
    handouts_issued: list = Field(default_factory=list)     # 已发放 handouts
    ending_reached: bool = False
    epilogue_done: bool = False
    end_vote: dict = Field(default_factory=dict)            # 结束投票
    budget_scale: float = 1.0                                # 上下文预算校准系数

    # —— 世界可达性与剧情闸（world_memory / navigation_service 读写）——
    blocked_scenes: dict = Field(default_factory=dict)      # 场景 id → 封路理由
    travel_suggested: list = Field(default_factory=list)    # KP 挂过「要前往 X 吗」的场景 id
    # 规划器裁定要揭示、但还没落到台账的线索（planned_effects 落实后清空）
    pending_clue_reveals: dict = Field(default_factory=dict)

    # —— 一次性折回通道：战斗/追逐结束时写入，KP 读一次续写余波后由生成流程清除 ——
    combat_result: dict | None = None
    # 已生成过配图的场景卡 key，用于去重（illustration_service）
    scene_cards: list = Field(default_factory=list)
