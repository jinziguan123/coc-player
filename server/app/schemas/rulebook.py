from datetime import datetime

from pydantic import BaseModel


class RulebookRead(BaseModel):
    id: str
    title: str
    rule_system: str
    page_count: int
    chunk_count: int
    status: str
    embed_model: str
    error: str
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class RuleHit(BaseModel):
    text: str
    page: int
    score: float
    rulebook_id: str


class RuleSearchResponse(BaseModel):
    query: str
    hits: list[RuleHit]


class VillageRulesRead(BaseModel):
    """村规的两种视图：``options`` 是显式改过的差异项，``effective`` 是叠在规则原文上的
    完整结果（面板拿它回显，才不必在前端复刻一份默认值表）。"""

    rule_system: str
    options: dict
    effective: dict


class VillageRulesUpdate(BaseModel):
    """改某套规则系统的村规。整份替换，``{}`` = 全改回规则原文。"""

    options: dict = {}
