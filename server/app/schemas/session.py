from __future__ import annotations

from datetime import datetime

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ParticipantInput(BaseModel):
    """开局时前端提交的一个席位。

    character_id 为空且 role=human 表示「留空待加入」的空席（claimed=false）。
    """

    character_id: str | None = None
    role: str = "ai"  # human | ai；KP 席由 kp_mode=human 自动创建
    is_primary: bool = False


class ParticipantRead(BaseModel):
    character_id: str | None = None
    role: str
    is_primary: bool
    seat_order: int
    claimed: bool = True
    ready: bool = True
    character_name: str | None = None
    is_mine: bool = False  # 该席位是否归当前请求 token 所有（由端点按 token 计算）
    is_host: bool = False  # 该席位是否房主（主角席 + 有 owner_token），端点按 token 计算
    is_online: bool = False  # 该席位玩家是否有活跃 /live 连接（端点按在线 token 计算）
    is_kp: bool = False  # 该席位是否为真人 KP

    model_config = {"from_attributes": True}


class SessionCreate(BaseModel):
    module_id: str
    # 旧单人路径：只传主角；新多席位路径：传 participants（含主角 + AI 队友 + 空席）
    player_character_id: str | None = None
    participants: list[ParticipantInput] | None = None
    kp_mode: str = "ai"  # ai | human

    @model_validator(mode="after")
    def _require_seat(self) -> "SessionCreate":
        if not self.participants and not self.player_character_id:
            raise ValueError("必须至少提供一个主角席位")
        if self.kp_mode not in ("ai", "human"):
            raise ValueError("kp_mode 必须是 ai 或 human")
        return self


class SessionRead(BaseModel):
    id: str
    module_id: str
    status: str
    kp_mode: str = "ai"
    identity_version: int = 1
    player_character_id: str | None
    room_code: str | None = None
    current_scene_id: str | None
    world_state: dict
    turn_state: dict | None
    # 本局的文风 / 画风（预设 id 或自定义原文）；""=继承模组默认值。见 services.style_presets
    narrative_style: str = ""
    image_style: str = ""
    participants: list[ParticipantRead] = []
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SessionStatusUpdate(BaseModel):
    status: str


class SessionStyleUpdate(BaseModel):
    """改本局的文风 / 画风。值为预设 id 或自定义原文，空串=改回「继承模组默认」。

    None = 本次不动该项（只想改画风时不必把文风也回传一遍）。
    """

    narrative_style: str | None = None
    image_style: str | None = None


class SessionRuleOptionsUpdate(BaseModel):
    """改本局家规。只提交想改的项；服务端会白名单化、钳进合法区间、只存与 RAW 的差异。

    ``{}`` = 全部改回 RAW（或模组默认）。取值语义见 rules.coc.options。
    """

    rule_options: dict = {}


class SessionRuleOptionsRead(BaseModel):
    """家规的两种视图：``options`` 是本局显式改过的差异项，``effective`` 是合并模组
    默认后的完整生效值（面板拿它回显，才不必在前端复刻一份默认值表）。"""

    options: dict
    effective: dict


class ClaimSeatRequest(BaseModel):
    seat_order: int
    # KP 席不绑定角色；human 玩家席仍必须提供角色。
    character_id: str | None = None


class SeatAddRequest(BaseModel):
    """大厅加座位：human=留给真人的空席，ai=待指派角色的 AI 席。"""

    role: str = "ai"


class SeatAssignRequest(BaseModel):
    """给 AI 席指派角色；None=清空（真人席的入座走 ClaimSeatRequest）。"""

    character_id: str | None = None


class ReadyRequest(BaseModel):
    ready: bool = True


class EndVoteRequest(BaseModel):
    """结束模组投票：以哪个真人席角色发起 / 同意；缺省取主角席。"""

    acting_character_id: str | None = None


class KpActionRequest(BaseModel):
    """真人 KP 工具桌动作；payload 由动作类型对应的表单字段组成。"""

    action: Literal[
        "narration", "dialogue", "dice_check", "opposed_check", "generic_roll", "san_check",
        "scene_change", "set_flag", "clear_flag", "handout", "hp_change",
        "start_combat",
    ]
    payload: dict = Field(default_factory=dict)
