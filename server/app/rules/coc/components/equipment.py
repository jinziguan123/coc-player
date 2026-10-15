"""coc7.equipment：装备目录（数据表 equipment；官方数据见 data/equipment.yaml）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field

from app.rules.config import RuleConfig, table_of
from app.rules.data import DataTable
from app.rules.kernel import Component, Provide


@dataclass
class Equipment:
    name: str
    category: str
    era: list[str] = field(default_factory=lambda: ["1920s", "modern"])
    min_credit: int = 0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "category": self.category,
            "era": self.era,
            "min_credit": self.min_credit,
        }


class EquipmentRow(BaseModel):
    name: str = Field(min_length=1, title="名称")
    category: str = Field(min_length=1, title="分类")
    era: list[str] = Field(default_factory=lambda: ["1920s", "modern"], title="适用时代",
                           description="1920s、modern 等")
    min_credit: int = Field(0, ge=0, le=99, title="最低信用评级")


EQUIPMENT = DataTable(
    name="equipment", title="装备表", row_model=EquipmentRow, key="name",
    source=Path(__file__).parent / "data" / "equipment.yaml", description="建卡时按时代与信用评级可选的随身装备",
    build=lambda rows: tuple(Equipment(**r) for r in rows),
)

#: 官方装备目录（未经任何补丁）。
COC_EQUIPMENT = EQUIPMENT.official


def equipment(options: RuleConfig | None = None) -> tuple[Equipment, ...]:
    """本局的装备目录（官方 + 模组 + 村规补丁）。"""
    return table_of(options, "equipment", COC_EQUIPMENT)


def get_available_equipment(
    era: str = "1920s", credit_rating: int = 0, options: RuleConfig | None = None,
) -> list[dict]:
    era_lower = era.lower().replace(" ", "")
    results = []
    for eq in equipment(options):
        era_match = any(e.lower().replace(" ", "") == era_lower for e in eq.era)
        if era_match and eq.min_credit <= credit_rating:
            results.append(eq.to_dict())
    return results


COMPONENT = Component(
    id="coc7.equipment", title="装备",
    requires=("coc7.character",),
    data=(EQUIPMENT,),
    provides={"equipment": Provide(__name__, bound=("equipment", "get_available_equipment"))},
)
