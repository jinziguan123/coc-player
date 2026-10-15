import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import require_local_client
from app.database import get_db
from app.models.rulebook import Rulebook
from app.rules import get_system
from app.schemas.rulebook import (
    HouseRulesExport,
    HouseRulesImportRequest,
    HouseRulesPreviewRead,
    RuleSearchResponse,
    RulebookRead,
    RulePluginList,
    RulePluginRead,
    RuleSystemManifest,
    RuleTableUpdate,
    RuleTableView,
    VillageRulesRead,
    VillageRulesUpdate,
)
from app.services import rule_options_service, rulebook_service

router = APIRouter(prefix="/api/rulebooks", tags=["rulebooks"])
logger = logging.getLogger(__name__)


def _ingest_bg(rulebook_id: str, pdf_bytes: bytes) -> None:
    """后台入库：自开会话（请求会话已随响应关闭），失败状态由 service 落库。"""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        book = db.get(Rulebook, rulebook_id)
        if book:
            rulebook_service.ingest_rulebook(db, book, pdf_bytes)
    finally:
        db.close()


@router.post("/upload", dependencies=[Depends(require_local_client)], response_model=RulebookRead)
async def upload_rulebook(
    background: BackgroundTasks,
    file: UploadFile,
    title: str | None = None,
    rule_system: str = "coc",
    db: Session = Depends(get_db),
):
    """上传规则书 PDF：立即建 indexing 记录并返回，嵌入索引在后台进行。

    前端可轮询 GET /api/rulebooks 观察 status 由 indexing → ready/failed。
    """
    content = await file.read()
    if not content:
        raise HTTPException(400, "空文件")
    name = (file.filename or "").lower()
    if not name.endswith(".pdf"):
        raise HTTPException(422, "目前只支持 PDF 规则书")

    book = Rulebook(
        title=title or file.filename or "规则书",
        rule_system=rule_system,
        status="indexing",
    )
    db.add(book)
    db.commit()
    db.refresh(book)

    background.add_task(_ingest_bg, book.id, content)
    return book


@router.get("", response_model=list[RulebookRead])
def list_rulebooks(db: Session = Depends(get_db)):
    return rulebook_service.list_rulebooks(db)


@router.get("/search", response_model=RuleSearchResponse)
def search_rules(
    q: str,
    rule_system: str = "coc",
    k: int = 3,
    db: Session = Depends(get_db),
):
    """调试用：直接对规则书做一次检索，观察命中片段与分数。"""
    hits = rulebook_service.retrieve(db, q, rule_system, k=k)
    return RuleSearchResponse(query=q, hits=hits)


@router.delete("/{rulebook_id}", dependencies=[Depends(require_local_client)])
def delete_rulebook(rulebook_id: str, db: Session = Depends(get_db)):
    if not rulebook_service.delete_rulebook(db, rulebook_id):
        raise HTTPException(404, "规则书不存在")
    return {"ok": True}


@router.get("/village-rules/{rule_system}", response_model=VillageRulesRead)
def read_village_rules(rule_system: str, db: Session = Depends(get_db)):
    """读某套规则系统的村规——这一桌长期沿用的规矩。

    不限本机：玩家有权知道自己在什么规则下掷骰。
    """
    # 面板要回显**存着的**配置，不能走 village_options——那个在开关关掉时按「没配过」
    # 返回空，界面会把用户辛苦配的一整套显示成默认值，一保存就真的没了。
    row = rule_options_service.village_row(db, rule_system)
    return VillageRulesRead(
        rule_system=rule_system,
        options=dict((row.options if row else None) or {}),
        effective=rule_options_service.village_view(db, rule_system),
        table_notes=(row.table_notes if row else "") or "",
        enabled=bool(row.enabled) if row else True,
        labels=rule_options_service.village_labels(db, rule_system),
        data=dict((row.data_patches if row else None) or {}),
    )


@router.put(
    "/village-rules/{rule_system}",
    dependencies=[Depends(require_local_client)],
    response_model=VillageRulesRead,
)
def update_village_rules(
    rule_system: str,
    data: VillageRulesUpdate,
    db: Session = Depends(get_db),
):
    """改某套规则系统的村规。整份替换；服务端白名单化、钳区间、只存与规则原文的差异。

    限本机：村规和规则书管理同属「这台机器上的桌面配置」，不该由联机进来的客人改动。
    改动对**所有**用这套规则的房间即时生效（含进行中的局）。
    """
    try:
        saved, notes = rule_options_service.save_village_options(
            db, rule_system, data.options, data.table_notes, data.enabled, data.data, data.tables,
        )
    except rule_options_service.RuleDataError as e:
        raise HTTPException(400, "\n".join(e.errors)) from e
    return VillageRulesRead(
        rule_system=rule_system,
        options=saved,
        effective=rule_options_service.village_view(db, rule_system),
        table_notes=notes,
        enabled=rule_options_service.village_enabled(db, rule_system),
        labels=rule_options_service.village_labels(db, rule_system),
        data=dict((rule_options_service.village_row(db, rule_system).data_patches) or {}),
    )


@router.get("/village-rules/{rule_system}/manifest", response_model=RuleSystemManifest)
def read_rule_manifest(rule_system: str):
    """规则系统的组件目录与各组件参数的 schema——规则页据此渲染，不再在前端写死字段。"""
    return RuleSystemManifest(**_system_or_404(rule_system).manifest())


@router.get("/village-rules/{rule_system}/export", response_model=HouseRulesExport)
def export_house_rules(rule_system: str, db: Session = Depends(get_db)):
    """把这一桌的村规导出成房规文件（YAML 原文 + 建议文件名），前端存成文件发给别人导入。

    不限本机：读村规本就公开，导出只是换个格式。
    """
    _system_or_404(rule_system)
    return HouseRulesExport(
        filename=f"house-rules-{rule_system}.yaml",
        text=rule_options_service.export_house_rules(db, rule_system),
    )


@router.post(
    "/village-rules/{rule_system}/import-preview",
    dependencies=[Depends(require_local_client)],
    response_model=HouseRulesPreviewRead,
)
def preview_house_rules(rule_system: str, data: HouseRulesImportRequest):
    """校验一份房规文件并返回预览，**不落库**。房主确认后前端走 PUT 保存。

    限本机：与改村规同属桌面配置操作。
    """
    _system_or_404(rule_system)
    preview, labels = rule_options_service.preview_house_rules(rule_system, data.text)
    return HouseRulesPreviewRead(
        ok=preview.ok, name=preview.name, description=preview.description,
        options=preview.options, table_notes=preview.table_notes, data=preview.data,
        tables=preview.tables, labels=labels,
        errors=preview.errors, warnings=preview.warnings,
    )


@router.get("/village-rules/{rule_system}/data/{table}", response_model=RuleTableView)
def read_village_table(rule_system: str, table: str, db: Session = Depends(get_db)):
    """数据表编辑器：这一桌对某张表的改动（合并后的行 + 每行状态 + 被删的官方行）。

    读的是**存着的**补丁，不看总开关——同村规参数面板，关掉开关不该让配置看起来没了。
    """
    _table_or_404(rule_system, table)
    row = rule_options_service.village_row(db, rule_system)
    patches = ((row.data_patches if row else None) or {}).get(table)
    return RuleTableView(table=table, **rule_options_service.table_view(rule_system, table, patches))


@router.put(
    "/village-rules/{rule_system}/data/{table}",
    dependencies=[Depends(require_local_client)],
    response_model=RuleTableView,
)
def update_village_table(
    rule_system: str, table: str, data: RuleTableUpdate, db: Session = Depends(get_db),
):
    """保存这一桌对某张表的改动：提交整张表，服务端校验并只存与官方数据的差异。

    限本机：与改村规同属桌面配置操作。对所有用这套规则的房间即时生效（含进行中的局）。
    """
    _table_or_404(rule_system, table)
    try:
        patches = rule_options_service.save_village_table(db, rule_system, table, data.rows)
    except rule_options_service.RuleDataError as e:
        raise HTTPException(400, "\n".join(e.errors)) from e
    return RuleTableView(table=table, **rule_options_service.table_view(rule_system, table, patches))


@router.get("/plugins/{rule_system}", response_model=RulePluginList)
def list_rule_plugins(rule_system: str):
    """本机安装的第三方规则插件及加载结果（含没加载的原因）。插件在应用启动时加载。"""
    from app.config import settings
    from app.rules import plugins

    _system_or_404(rule_system)
    return RulePluginList(
        directory=str(settings.plugins_dir),
        api_version=plugins.PLUGIN_API_VERSION,
        plugins=[RulePluginRead(**vars(i)) for i in plugins.installed(rule_system)],
    )


def _table_or_404(rule_system: str, table: str):
    try:
        return _system_or_404(rule_system).data_table(table)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


def _system_or_404(rule_system: str):
    try:
        return get_system(rule_system)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
