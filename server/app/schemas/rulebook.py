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


class RuleTableView(BaseModel):
    """数据表编辑器读一层补丁：合并后的行、每行状态（official/modified/added）、被删掉的官方行。"""

    table: str
    rows: list[dict]
    status: dict[str, str]
    deleted: list[dict] = []
    #: 被改过的官方行的原样（主键 → 行），供「恢复官方」
    originals: dict[str, dict] = {}
    patches: list[dict] = []


class RuleTableUpdate(BaseModel):
    """编辑器保存：提交想要的**整张表**，服务端校验并算出相对官方数据的最小补丁。"""

    rows: list[dict]


class VillageRulesUpdate(BaseModel):
    """改某套规则系统的村规。整份替换，``{}`` = 全改回规则原文。

    ``table_notes=None`` 表示本次不动桌面约定（只改参数时不必把整段文字回传一遍）。
    """

    options: dict = {}
    table_notes: str | None = None
    #: ``None`` = 本次不动总开关（只改参数时不必把它一并回传）
    enabled: bool | None = None
    #: 数据表补丁（表名 → 补丁行），整份替换；``None`` = 本次不动（导入房规时才带）
    data: dict | None = None


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
    #: 数据表补丁（表名 → 补丁行），已严格校验
    data: dict = {}
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
    #: 组件来源：official 或 plugin:<插件文件名>
    source: str = "official"
    requires: list[str] = []
    conflicts: list[str] = []
    #: 本组件参数的 JSON Schema（含 x-choices / x-show-if 界面提示）；无参数时为 None
    config_schema: dict | None = None


class RuleTableManifest(BaseModel):
    name: str
    title: str
    description: str = ""
    #: 行主键字段（编辑器据此对齐、查重）
    key: str
    #: 声明这张表的组件
    component: str
    #: 行的 JSON Schema（编辑器据此生成表单）
    row_schema: dict


class RuleSystemManifest(BaseModel):
    """规则系统的组件目录：前端村规面板与数据表编辑器据此渲染，房规文件据此校验。"""

    id: str
    title: str
    version: int
    house_rules_format_version: int
    tables: list[RuleTableManifest] = []
    components: list[RuleComponentManifest]


class RulePluginRead(BaseModel):
    """一个第三方规则插件的加载结果。"""

    file: str
    name: str = ""
    version: str = ""
    description: str = ""
    #: loaded | failed
    status: str
    #: 没加载的原因（导入报错、接口版本不符、与官方或其他插件冲突……）
    error: str = ""
    #: 插件提供的组件 id
    components: list[str] = []


class RulePluginList(BaseModel):
    #: 插件目录（放插件文件的位置）
    directory: str
    #: 本应用的插件接口版本
    api_version: int
    plugins: list[RulePluginRead]
