import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response, UploadFile, File
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import player_token
from app.api.deps import require_local_client
from app.database import get_db
from app.models.module import Module
from app.schemas.character import (
    ApplyAgeRequest,
    ApplyAgeResponse,
    BaseSkillsRequest,
    BaseSkillsResponse,
    DerivedStatsRequest,
    DerivedStatsResponse,
    CharacterCreate,
    CharacterRead,
    CharacterUpdate,
    RollAttributesResponse,
)
from app.rules import Ruleset
from app.services import (
    ai_character_service,
    character_avatar,
    character_service,
    image_store,
    rate_limit,
    rule_options_service,
    session_service,
)
from app.services.rate_limit import limiter
from app.services.excel_import import parse_coc_character_sheet

router = APIRouter(prefix="/api", tags=["characters"])


def _rules(rule_system: str, module_id: str | None = None, db: Session = Depends(get_db)) -> Ruleset:
    """建卡与规则资料接口用的生效规则：这一桌的村规（参数 + 数据表），给了 module_id 再叠上
    该模组自带的数据（如模组专属的职业、武器）。系统未注册按 400「暂不支持」回。"""
    module = db.get(Module, module_id) if module_id else None
    try:
        return rule_options_service.village_ruleset(db, rule_system, module)
    except ValueError:
        raise HTTPException(400, f"暂不支持 {rule_system}")


def _slot(rs: Ruleset, slot: str, what: str = ""):
    """取某个服务槽位；这套规则系统没有这块规则时按 400「暂不支持」回。"""
    if not rs.has(slot):
        raise HTTPException(400, f"暂不支持 {rs.system}{f' 的{what}' if what else ''}")
    return getattr(rs, slot)


@router.get("/rules/{rule_system}/character-schema")
def get_character_schema(rs: Ruleset = Depends(_rules)):
    return _slot(rs, "character").get_character_schema()


@router.get("/rules/{rule_system}/occupations")
def get_occupations(rs: Ruleset = Depends(_rules)):
    occ = _slot(rs, "occupation", "职业列表")
    return [
        {
            "name": o.name,
            "credit_min": o.credit_min,
            "credit_max": o.credit_max,
            "skill_formula": o.skill_formula,
            "skills": o.skills,
            "groups": [{"label": g.label, "pick": g.pick, "options": g.options} for g in o.groups],
            "choices": o.choices,
            "category": o.category,
        }
        for o in occ.occupations()
    ]


@router.get("/rules/{rule_system}/creation-rules")
def get_creation_rules(rs: Ruleset = Depends(_rules)):
    """建卡界面要的规则常量：本桌的技能上限、点数购买总额、快速车卡的定值。"""
    c = _slot(rs, "creation", "建卡规则")
    opts = rs.options
    return {
        "skill_cap_occupation": opts.skill_cap_occupation,
        "skill_cap_other": opts.skill_cap_other,
        "point_buy_total": c.POINT_BUY_TOTAL,
        "quick_attrs": list(c.QUICK_ATTRS),
        "quick_skills": list(c.QUICK_SKILLS),
        "quick_boosts": c.QUICK_BOOSTS,
        "quick_boost": c.QUICK_BOOST,
    }


@router.get("/rules/{rule_system}/occupation-categories")
def get_occupation_categories(rs: Ruleset = Depends(_rules)):
    """职业大类的展示顺序（前端两级选择用）：官方大类在前，房主/模组新增的追加。"""
    return _slot(rs, "occupation").occupation_categories()


@router.get("/rules/{rule_system}/statuses")
def get_statuses(rs: Ruleset = Depends(_rules)):
    """角色状态可选项（正常/重伤/昏迷/死亡/临时·不定期·永久疯狂）。"""
    return _slot(rs, "wound").CHARACTER_STATUSES


class SkillPointsRequest(BaseModel):
    occupation: str
    base_attributes: dict[str, int]


@router.post("/rules/{rule_system}/calc-skill-points")
def calc_skill_points(data: SkillPointsRequest, rs: Ruleset = Depends(_rules)):
    occ = _slot(rs, "occupation")
    return {
        "occupation_points": occ.calc_occupation_points(data.occupation, data.base_attributes),
        "interest_points": occ.calc_interest_points(data.base_attributes),
    }


@router.get("/rules/{rule_system}/equipment")
def get_equipment(era: str = "1920s", credit_rating: int = 0, rs: Ruleset = Depends(_rules)):
    return _slot(rs, "equipment").get_available_equipment(era, credit_rating)


@router.get("/rules/{rule_system}/weapons")
def get_weapons(rs: Ruleset = Depends(_rules)):
    """本桌的武器表 + 大类展示顺序，供武器选择器两级筛选使用。"""
    combat = _slot(rs, "combat", "武器表")
    return {"weapons": list(combat.weapons()), "categories": combat.weapon_categories()}


@router.get("/rules/{rule_system}/specializations")
def get_specializations(rs: Ruleset = Depends(_rules)):
    """专精技能类别（母语/外语/格斗/射击/科学/生存/技艺/驾驶）及各专精起始值。"""
    spec = _slot(rs, "occupation", "专精列表")
    return {"categories": spec.specializations(), "single": sorted(spec.SINGLE_SPEC)}


class EvaluateRequest(BaseModel):
    module_id: str
    name: str
    occupation: str = ""
    backstory: str = ""


@router.post("/characters/evaluate")
@limiter.limit(rate_limit.AI_TRIGGER_LIMIT, exempt_when=rate_limit.exempt_local)
async def evaluate_character(
    request: Request,  # noqa: ARG001 — slowapi 要求端点显式声明才能取到请求
    response: Response,  # noqa: ARG001 — headers_enabled 下 slowapi 往这里写 X-RateLimit-*
    data: EvaluateRequest,
    db: Session = Depends(get_db),
):
    module = db.get(Module, data.module_id)
    if not module:
        raise HTTPException(404, "模组不存在")

    era = (module.world_setting or {}).get("era", "未知")
    era_tag = (module.world_setting or {}).get("era", "")

    from app.ai.llm_factory import get_llm
    llm = get_llm()
    prompt = (
        f"你是一个 TRPG 角色审核专家。请评估以下角色是否适合参与指定的模组。\n\n"
        f"模组信息：\n- 标题：{module.title}\n- 年代：{era_tag or era}\n"
        f"- 描述：{module.description}\n\n"
        f"角色信息：\n- 名字：{data.name}\n- 职业：{data.occupation or '未知'}\n"
        f"- 背景故事：{data.backstory or '无'}\n\n"
        f"请检查：\n"
        f"1. 角色的职业和背景是否符合模组的时代背景（例如1920s不应有现代科技产物）\n"
        f"2. 背景中提到的物品、装备是否在该时代合理\n"
        f"3. 角色概念是否与模组基调匹配\n\n"
        f'以 JSON 格式返回：\n{{"compatible": true或false, "warnings": ["不合理之处"], "suggestions": ["建议"]}}\n'
        f"如果没有问题，warnings 和 suggestions 为空数组。"
    )
    result = await llm.complete(
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.3,
    )
    return json.loads(result)


@router.post("/characters/import-excel", dependencies=[Depends(require_local_client)])
async def import_from_excel(
    module_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    if not file.filename or not file.filename.endswith(".xlsx"):
        raise HTTPException(400, "请上传 .xlsx 格式的 Excel 文件")

    content = await file.read()
    try:
        parsed = parse_coc_character_sheet(content)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception:
        raise HTTPException(400, "Excel 解析失败，请确认是 COC 七版角色卡格式")

    return {
        **parsed,
        "module_id": module_id,
        "rule_system": "coc",
    }


class AIGenerateRequest(BaseModel):
    module_id: str
    hint: str = ""
    is_player: bool = False


@router.post("/characters/ai-generate")
@limiter.limit(rate_limit.AI_TRIGGER_LIMIT, exempt_when=rate_limit.exempt_local)
async def ai_generate_character(
    request: Request,  # noqa: ARG001 — slowapi 要求端点显式声明才能取到请求
    response: Response,  # noqa: ARG001 — headers_enabled 下 slowapi 往这里写 X-RateLimit-*
    data: AIGenerateRequest,
    db: Session = Depends(get_db),
):
    try:
        result = await ai_character_service.generate_ai_character(
            db, data.module_id, data.hint, data.is_player,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception:
        raise HTTPException(502, "AI 生成失败，请重试")
    return {**result, "module_id": data.module_id, "rule_system": "coc"}


@router.post("/characters/roll-attributes", response_model=RollAttributesResponse)
def roll_attributes(rule_system: str = "coc", count: int = 3):
    try:
        sets = character_service.roll_attribute_sets(rule_system, count)
        return RollAttributesResponse(sets=sets)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/rules/{rule_system}/apply-age", response_model=ApplyAgeResponse)
def apply_age(data: ApplyAgeRequest, rs: Ruleset = Depends(_rules)):
    """按年龄修正属性并掷幸运（CoC 7e 建卡第 3 步）。

    EDU 增强检定由服务端自动掷，掷点写进 notes——面团习惯是玩家自己掷，
    但这里是单机流程，把骰点亮出来比来回问一次更顺，也照样看得见运气好坏。
    """
    character = _slot(rs, "character")
    attrs, notes = character.apply_age_modifiers(data.base_attributes, data.age)
    luck, luck_rolls = character.roll_luck(data.age)
    return ApplyAgeResponse(
        base_attributes=attrs, notes=notes, luck=luck, luck_rolls=luck_rolls,
    )


@router.post("/rules/{rule_system}/derived", response_model=DerivedStatsResponse)
def derived_stats(data: DerivedStatsRequest, rs: Ruleset = Depends(_rules)):
    """建卡预览的派生值，按这一桌的规则算（含 Pulp 英雄生命值等变体）。

    前端有一份本地镜像（cocDerived.ts）用于即时预览；本接口是权威值，变体打开时以它为准。
    """
    attrs = data.base_attributes
    d = _slot(rs, "character").compute_derived(attrs, data.age)
    return DerivedStatsResponse(
        hp=d["hitPoints"]["max"], mp=d["magicPoints"]["max"], san=d["sanity"]["current"],
        mov=d["move"], db=d["damageBonus"], build=d["build"], dodge=attrs.get("DEX", 50) // 2,
    )


@router.post("/rules/{rule_system}/base-skills", response_model=BaseSkillsResponse)
def base_skills(data: BaseSkillsRequest, rs: Ruleset = Depends(_rules)):
    """这组属性下、未加任何点时的技能起始值（母语=EDU、闪避=DEX//2 已填好）。

    建卡界面必须拿这一份而不是 character-schema 里的 default_skills：后者的母语/闪避
    是占位的 0，界面照着 0 展示，玩家为了把它填上去而花掉的点，落库时会被兜底的
    `max(提交值, 派生值)` 顶掉，凭空蒸发。同一条规则只在 coc7.character 组件里实现一次。
    """
    try:
        character = _slot(rs, "character")
        return BaseSkillsResponse(skills=character.base_skills(data.base_attributes))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/characters", response_model=CharacterRead)
def create_character(
    data: CharacterCreate,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    try:
        # 谁建的卡就归谁，不再看 is_player。
        #
        # 从前「AI 队友卡不绑归属」，于是它对全网可见——那是为了让房主能在 AI 席下拉里
        # 挑到它。可归属与「谁来演」是两回事：卡归建它的人，演它的是坐上去的那个席位。
        # 按 is_player 决定绑不绑，等于让「建卡时点了哪个按钮」顺带决定这张卡的可见范围。
        payload = data.model_dump()
        if token:
            payload["owner_token"] = token
        char = character_service.create_character(db, payload)
        return char
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/characters", response_model=list[CharacterRead])
def list_characters(
    module_id: str | None = None,
    available: bool = False,
    is_player: bool | None = None,
    mine: bool = False,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    chars = character_service.list_characters(db, module_id)
    # 别人的角色卡不出现在你的库里。
    #
    # 客人入座时会在房主机器上留一份参战副本——房主的规则引擎要读写它才跑得动
    # （检定读技能值、HP_CHANGE 改血、物品与成长都落在角色记录上），而
    # SessionParticipant.character_id 是指向本地 characters 表的外键。但那是
    # **会话资产**，不是房主的藏品：此前房主打开「角色」页会看到一堆队友的卡，
    # 还能删改它们。
    #
    # 无主的卡（AI 队友、identity 机制之前的旧数据）仍然可见，否则房主会突然
    # 看不到自己以前建的角色。
    chars = [c for c in chars if not c.owner_token or c.owner_token == token]
    if is_player is not None:
        # 保留这个查询参数只为不打断旧客户端；站内已经不再按它分池——一张卡是给真人演
        # 还是给 AI 演，是**席位**的事，不是卡的属性（谁驱动看的是 SessionParticipant.role）。
        # 按它分池的直接后果是：同一张卡因为建卡时点了哪个按钮，就永久进不了另一种席位。
        chars = [c for c in chars if c.is_player == is_player]
    if mine:
        # 「我的」= 我 token 名下的 + **无主的**（认领席位时用）。
        #
        # 无主那一半不能漏：上面那道通用过滤（`not c.owner_token or ...`）就明确放行了它们，
        # 理由是「否则房主会突然看不到自己以前建的角色」。这里如果反过来要求 owner_token
        # 非空，同一批卡就会出现「角色页看得见、进大厅却选不了」的自相矛盾——而实测库里
        # 24 张卡有 12 张无主（identity 机制之前建的、清过 localStorage 换了 token 的、
        # AI 生成的），用户会看到「我建了一堆角色，待选却只有三个」。
        #
        # 暴露面没有变大：无主的卡本来就对所有人可见（见上），这里只是不再比它更严。
        chars = [c for c in chars if not c.owner_token or c.owner_token == token]
    if available:
        occupied = session_service.active_character_ids(db)
        chars = [c for c in chars if c.id not in occupied]
    return chars


@router.get("/characters/{character_id}", response_model=CharacterRead)
def get_character(
    character_id: str,
    request: Request,
    db: Session = Depends(get_db),
    token: str | None = Depends(player_token),
):
    char = character_service.get_character(db, character_id)
    if not char:
        raise HTTPException(404, "角色不存在")
    # 房主本机看自己库里的一切；客人只能看无主卡、自己的卡与同桌队友的卡。
    # 不在这里把关的话，列表接口刻意藏起来的别人的卡，拿 id 就能整张读走。
    if not rate_limit.exempt_local(request) and not session_service.can_view_character(
        db, char, token,
    ):
        raise HTTPException(403, "无权查看该角色")
    return char


@router.post(
    "/characters/{character_id}/avatar",
    dependencies=[Depends(require_local_client)],
    response_model=CharacterRead,
)
async def upload_character_avatar(
    character_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """手动上传角色头像。

    与 AI 生成走同一条落盘与回写路径，因此两者产出的头像在系统里毫无区别——没配生图模型
    的人照样能有头像，对生成结果不满意的人也不必反复重掷。
    """
    char = character_service.get_character(db, character_id)
    if not char:
        raise HTTPException(404, "角色不存在")
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "上传的文件是空的")
    if len(raw) > image_store.MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"图片过大（上限 {image_store.MAX_UPLOAD_BYTES // 1024 // 1024}MB）")
    url = image_store.save_image_bytes(raw)
    if not url:
        raise HTTPException(422, "无法识别这个文件，请换一张常见格式的图片（JPG / PNG / WebP）")
    return character_avatar.set_avatar(db, char, url)


@router.post(
    "/characters/{character_id}/avatar/generate",
    dependencies=[Depends(require_local_client)],
    response_model=CharacterRead,
)
async def generate_character_avatar(character_id: str, db: Session = Depends(get_db)):
    """按需 AI 生成角色头像（手动触发，不在建卡流程里自动跑）。"""
    char = character_service.get_character(db, character_id)
    if not char:
        raise HTTPException(404, "角色不存在")
    url = await character_avatar.generate_avatar(db, char)
    if not url:
        raise HTTPException(
            422,
            "头像生成失败：请确认已在设置页配置可用的生图模型，或稍后重试；"
            "也可以直接上传一张图片。",
        )
    return char


@router.delete(
    "/characters/{character_id}/avatar",
    dependencies=[Depends(require_local_client)],
    response_model=CharacterRead,
)
def clear_character_avatar(character_id: str, db: Session = Depends(get_db)):
    """摘掉头像，回到姓名首字纹章（那是正常样式，不是缺陷态）。"""
    char = character_service.get_character(db, character_id)
    if not char:
        raise HTTPException(404, "角色不存在")
    return character_avatar.set_avatar(db, char, None)


@router.delete("/characters/{character_id}", dependencies=[Depends(require_local_client)])
def delete_character(character_id: str, db: Session = Depends(get_db)):
    if not character_service.delete_character(db, character_id):
        raise HTTPException(404, "角色不存在")
    return {"ok": True}


@router.put("/characters/{character_id}", dependencies=[Depends(require_local_client)], response_model=CharacterRead)
def update_character(
    character_id: str, data: CharacterUpdate, db: Session = Depends(get_db)
):
    updates = data.model_dump(exclude_unset=True)
    current = character_service.get_character(db, character_id)
    if not current:
        raise HTTPException(404, "角色不存在")
    if updates.get("base_attributes") is not None or updates.get("skills") is not None:
        # 编辑不受建卡规则约束（团中成长过的卡本就超出建卡点数），但基本范围要守住
        rs = rule_options_service.village_ruleset(db, current.rule_system)
        if rs.has("character"):
            ok, errors = rs.character.validate_character({
                "base_attributes": updates.get("base_attributes") or current.base_attributes or {},
                "skills": updates.get("skills") or current.skills or {},
            })
            if not ok:
                raise HTTPException(400, "角色数据校验失败：" + "，".join(errors))
    char = character_service.update_character(db, character_id, updates)
    return char
