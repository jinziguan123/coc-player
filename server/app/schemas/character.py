from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class CharacterCreate(BaseModel):
    name: str
    # 可留空：角色卡不必属于某个模组。
    #
    # 联机时客人的角色卡存在**他自己**的库里（ADR-007：素材库的写操作只允许本机），
    # 而模组在房主库里——跨库引用没有意义。此前这里必填，于是本地没有对应模组的
    # 客人根本建不了卡。模型层与 CharacterRead 一直是可空的，只有这里卡着。
    module_id: str | None = None
    # 参战副本指回客人库里的原件；本机自建的卡留空。
    origin_character_id: str | None = None
    rule_system: str
    is_player: bool = True
    age: int = 25
    base_attributes: dict[str, int] = {}
    skills: dict[str, int] = {}
    system_data: dict = {}
    backstory: str = ""


class CharacterRead(BaseModel):
    id: str
    name: str
    module_id: str | None
    # 有值即为参战副本，客人据此把本局结果写回自己的原件。
    origin_character_id: str | None = None
    rule_system: str
    is_player: bool
    base_attributes: dict
    skills: dict
    system_data: dict
    backstory: str
    status: str
    avatar_url: str | None = None
    # 模组经历：结局后由系统追加，前端只读（档案卡的「已归档 N 篇」与经历视图用它）
    experiences: list = []
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CharacterUpdate(BaseModel):
    name: str | None = None
    base_attributes: dict | None = None
    skills: dict | None = None
    system_data: dict | None = None
    backstory: str | None = None
    status: str | None = None
    # 允许置空（摘掉头像回到首字纹章）；经历不在这里改，只由结局流程追加
    avatar_url: str | None = None


class RollAttributesResponse(BaseModel):
    sets: list[dict[str, int]]


class ApplyAgeRequest(BaseModel):
    base_attributes: dict[str, int]
    age: int = 25


class BaseSkillsRequest(BaseModel):
    base_attributes: dict[str, int]


class BaseSkillsResponse(BaseModel):
    """这组属性下、未加任何点时的技能起始值（含属性派生项）。"""

    skills: dict[str, int]


class AgeNote(BaseModel):
    """一条年龄修正明细，供界面把「属性为什么变了」摊开给玩家看。"""

    label: str
    detail: str
    delta: int


class ApplyAgeResponse(BaseModel):
    base_attributes: dict[str, int]
    notes: list[AgeNote]
    #: 幸运一并在这里掷——15-19 岁「两次取高」也是年龄规则的一部分
    luck: int
    luck_rolls: list[int]
