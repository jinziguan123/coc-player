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
    #: 桌面约定原文（参数表达不了的规矩）；只影响叙述与裁定倾向，不改骰子结算
    table_notes: str = ""
    #: 村规总开关。关掉时上面的 options/table_notes 一律按「没配过」生效（配置本身还留着）
    enabled: bool = True
    #: ``options`` 与规则原文不同之处的短标签（规矩摘要用；由各规则组件生成，前端不再自己翻译）
    labels: list[str] = []


class VillageRulesUpdate(BaseModel):
    """改某套规则系统的村规。整份替换，``{}`` = 全改回规则原文。

    ``table_notes=None`` 表示本次不动桌面约定（只改参数时不必把整段文字回传一遍）。
    """

    options: dict = {}
    table_notes: str | None = None
    #: ``None`` = 本次不动总开关（只改参数时不必把它一并回传）
    enabled: bool | None = None


class HouseRulesExport(BaseModel):
    """房规文件：YAML 原文与建议的文件名。"""

    filename: str
    text: str


class HouseRulesImportRequest(BaseModel):
    """导入房规：文件原文（YAML）。只预览、不落库。"""

    text: str


class HouseRulesPreviewRead(BaseModel):
    """房规文件的导入预览。``errors`` 为空才能保存；``warnings`` 只是提示。"""

    ok: bool
    name: str = ""
    description: str = ""
    options: dict = {}
    table_notes: str = ""
    labels: list[str] = []
    errors: list[str] = []
    warnings: list[str] = []


class RuleComponentManifest(BaseModel):
    id: str
    title: str
    description: str = ""
    #: 村规面板上的分栏；空串 = 不在面板上出现（没有可调参数的常驻组件）
    group: str = ""
    #: 可选组件的开关参数名；None = 常驻组件
    toggle: str | None = None
    requires: list[str] = []
    conflicts: list[str] = []
    #: 本组件参数的 JSON Schema（含 x-choices / x-show-if 界面提示）；无参数时为 None
    config_schema: dict | None = None


class RuleSystemManifest(BaseModel):
    """规则系统的组件目录：前端村规面板据此渲染，房规文件据此校验。"""

    id: str
    title: str
    version: int
    house_rules_format_version: int
    components: list[RuleComponentManifest]
