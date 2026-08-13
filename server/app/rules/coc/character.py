"""CoC 7th Edition 角色创建逻辑"""

from app.rules.dice import roll

# CoC 7th 基础属性掷骰规则
ATTRIBUTE_ROLLS = {
    "STR": "3d6",   # 力量
    "CON": "3d6",   # 体质
    "SIZ": "2d6",   # 体型 (2d6+6)*5
    "DEX": "3d6",   # 敏捷
    "APP": "3d6",   # 外貌
    "INT": "2d6",   # 智力 (2d6+6)*5
    "POW": "3d6",   # 意志
    "EDU": "2d6",   # 教育 (2d6+6)*5
}

# 2d6+6 的属性列表
PLUS_SIX_ATTRS = {"SIZ", "INT", "EDU"}

COC_DEFAULT_SKILLS: dict[str, int] = {
    "会计": 5, "人类学": 1, "估价": 5, "考古学": 1, "取悦": 15,
    "攀爬": 20, "计算机使用": 5, "信用评级": 0, "克苏鲁神话": 0,
    "乔装": 5, "闪避": 0, "驾驶": 20, "汽车驾驶": 20, "电气维修": 10, "电子学": 1,
    "话术": 5, "格斗(斗殴)": 25, "射击(手枪)": 20, "射击(步枪)": 25,
    "急救": 30, "历史": 5, "恐吓": 15, "跳跃": 20, "母语": 0,
    "法律": 5, "图书馆使用": 20, "聆听": 20, "锁匠": 1, "机械维修": 10,
    "医学": 1, "博物学": 10, "导航": 10, "神秘学": 5, "操作重型机械": 1,
    "说服": 10, "摄影": 5, "精神分析": 1, "心理学": 10, "骑术": 5,
    "科学": 1, "妙手": 10, "侦查": 25, "潜行": 20, "游泳": 20, "潜水": 1,
    "投掷": 20, "追踪": 10, "驯兽": 5,
    # 专精占位（运行时可经专精弹窗细化为「外语(法语)」等）
    "外语": 1, "生存": 5, "技艺": 5,
}


def roll_attributes() -> dict[str, int]:
    """掷一组 CoC 基础属性"""
    attrs = {}
    for name, notation in ATTRIBUTE_ROLLS.items():
        result = roll(notation)
        if name in PLUS_SIX_ATTRS:
            attrs[name] = (result.total + 6) * 5
        else:
            attrs[name] = result.total * 5
    return attrs


# ── 年龄修正（CoC 7e 建卡第 3 步）────────────────────────────────────────
#
# 这是 7 版建卡里最有味道的一步：年龄不是标签，是取舍——老角色学识深厚但体能衰退。
# 缺了它，70 岁的教授和 25 岁的运动员除了移动力毫无差别，年龄字段等于装饰。
#
# 每档给 (EDU 增强检定次数, 体能减值总额, APP 减值)。体能减值按规则由玩家在
# STR/CON/DEX 间自行分配，这里按「尽量平均、余数从 STR 起依次多扣」自动摊派，
# 保证总额与规则一致；玩家想换分配方式可在角色卡编辑页改。
_AGE_BANDS: tuple[tuple[int, int, int, int], ...] = (
    # (年龄上限, EDU 增强次数, STR/CON/DEX 合计减值, APP 减值)
    (19, 0, 0, 0),      # 15-19 另有 EDU-5、STR/SIZ-5、幸运两次取高，见下
    (39, 1, 0, 0),
    (49, 2, 5, 5),
    (59, 3, 10, 10),
    (69, 4, 20, 15),
    (79, 4, 40, 20),
    (89, 4, 80, 25),
)
_PHYSICAL_KEYS = ("STR", "CON", "DEX")
#: 属性下限：减值再狠也不该把人扣成负数或 0（0 在 CoC 里意味着已经不是活人了）。
_ATTR_FLOOR = 1
EDU_MAX = 99


def _band(age: int) -> tuple[int, int, int]:
    for ceiling, edu_rolls, physical, app_penalty in _AGE_BANDS:
        if age <= ceiling:
            return edu_rolls, physical, app_penalty
    return _AGE_BANDS[-1][1:]          # 90+ 沿用 80-89 档


def _spread(total: int, keys: tuple[str, ...]) -> dict[str, int]:
    """把减值总额摊到几个属性上：尽量平均，余数从头依次多扣一点。"""
    if total <= 0:
        return {}
    base, rest = divmod(total, len(keys))
    return {k: base + (1 if i < rest else 0) for i, k in enumerate(keys)}


def apply_age_modifiers(
    attrs: dict[str, int], age: int, *, roller=None,
) -> tuple[dict[str, int], list[dict]]:
    """按年龄修正属性，返回 (新属性, 明细)。

    明细每条形如 ``{"label", "detail", "delta"}``，供界面把「为什么变了」摊开给玩家看——
    建卡时属性被悄悄改掉是最容易让人觉得系统在乱来的地方。

    ``roller`` 仅供测试注入（签名同 ``roll``）。
    """
    r = roller or roll
    out = dict(attrs)
    notes: list[dict] = []
    edu_rolls, physical, app_penalty = _band(age)

    def _dec(key: str, amount: int) -> int:
        """扣减并返回**实际**扣掉的量（受下限约束）。"""
        if amount <= 0:
            return 0
        before = out.get(key, 0)
        out[key] = max(_ATTR_FLOOR, before - amount)
        return before - out[key]

    if age <= 19:
        # 少年：教育尚浅、身体没长开，但运气特别好
        actual = _dec("EDU", 5)
        if actual:
            notes.append({"label": "未成年", "detail": "教育 −5", "delta": -actual})
        # STR 与 SIZ 合计 −5，同样按摊派处理
        for key, amount in _spread(5, ("STR", "SIZ")).items():
            got = _dec(key, amount)
            if got:
                notes.append({"label": "未成年", "detail": f"{key} −{got}", "delta": -got})
    else:
        for key, amount in _spread(physical, _PHYSICAL_KEYS).items():
            got = _dec(key, amount)
            if got:
                notes.append({"label": "年龄衰退", "detail": f"{key} −{got}", "delta": -got})
        got = _dec("APP", app_penalty)
        if got:
            notes.append({"label": "年龄衰退", "detail": f"APP −{got}", "delta": -got})

    # 教育增强检定：每次 D100 > 当前 EDU 则 EDU + 1D10，封顶 99。
    # 掷点写进明细——玩家看得见自己是怎么涨上去的，比凭空多几点可信得多。
    for i in range(edu_rolls):
        check = r("1d100").total
        cur = out.get("EDU", 0)
        if check > cur:
            gain = r("1d10").total
            after = min(EDU_MAX, cur + gain)
            real = after - cur
            out["EDU"] = after
            notes.append({
                "label": f"教育增强 {i + 1}",
                "detail": f"D100={check} > 教育{cur}，+{gain}" + ("（封顶 99）" if real < gain else ""),
                "delta": real,
            })
        else:
            notes.append({
                "label": f"教育增强 {i + 1}",
                "detail": f"D100={check} ≤ 教育{cur}，未提升",
                "delta": 0,
            })
    return out, notes


def roll_luck(age: int = 25, *, roller=None) -> tuple[int, list[int]]:
    """掷幸运（3D6×5）。15-19 岁掷两次取高——这是给少年角色的补偿。

    返回 (幸运值, 各次掷出的原始值)，第二个值供界面说明「两次取高」。
    """
    r = roller or roll
    rolls = [r("3d6").total * 5]
    if age <= 19:
        rolls.append(r("3d6").total * 5)
    return max(rolls), rolls


#: 伤害加值 / 体格表（按 STR+SIZ）。7 版在 164 以上每 80 点进一档，
#: 早先的实现最后一档是 else，165 往上全归 1D6——玩家角色很难超 204，但 NPC 与
#: 怪物会，而战斗引擎用的是同一套派生。
_DAMAGE_BONUS_TABLE: tuple[tuple[int, str, int], ...] = (
    (64, "-2", -2),
    (84, "-1", -1),
    (124, "0", 0),
    (164, "1d4", 1),
    (204, "1d6", 2),
    (284, "2d6", 3),
    (364, "3d6", 4),
    (444, "4d6", 5),
)


def damage_bonus(combined: int) -> tuple[str, int]:
    """按 STR+SIZ 取 (伤害加值骰式, 体格)。超出表尾时每 80 点续进一档。"""
    for ceiling, db, build in _DAMAGE_BONUS_TABLE:
        if combined <= ceiling:
            return db, build
    # 444 以上：每满 80 点多一个 D6（体格同步 +1），巨型怪物用得上
    extra = (combined - 445) // 80 + 1
    return f"{4 + extra}d6", 5 + extra


def compute_derived(attrs: dict[str, int], age: int = 25) -> dict:
    """计算派生属性"""
    str_val = attrs.get("STR", 50)
    con_val = attrs.get("CON", 50)
    siz_val = attrs.get("SIZ", 50)
    dex_val = attrs.get("DEX", 50)
    pow_val = attrs.get("POW", 50)

    hp = (con_val + siz_val) // 10
    mp = pow_val // 5
    san = pow_val
    # 走 roll_luck 而非裸 3d6：未成年「两次取高」是年龄规则的一部分，
    # 不能只在新建卡流程里生效、老路径悄悄漏掉。
    luck, _ = roll_luck(age)

    # 移动力：7 版三档。
    # 注意第二、三档的判据不同——「任一 ≥ SIZ」是 8，「两者都 > SIZ」才是 9。
    # 早先第二档写成 `dex >= siz or str >= siz`，把第三档的情况全吞了，MOV 永远算不出 9
    # （穷举 64 组属性验证过一次都没出现）。
    if str_val > siz_val and dex_val > siz_val:
        mov = 9
    elif str_val < siz_val and dex_val < siz_val:
        mov = 7
    else:
        mov = 8

    if age >= 80:
        mov -= 5
    elif age >= 70:
        mov -= 4
    elif age >= 60:
        mov -= 3
    elif age >= 50:
        mov -= 2
    elif age >= 40:
        mov -= 1

    db, build = damage_bonus(str_val + siz_val)

    return {
        "hitPoints": {"current": hp, "max": hp},
        "magicPoints": {"current": mp, "max": mp},
        "sanity": {"current": san, "max": 99},
        "luck": luck,
        "move": mov,
        "damageBonus": db,
        "build": build,
        "age": age,
        "occupation": "",
    }


def asset_tier(cr: int) -> str:
    """信用评级 → 财富等级。"""
    if cr <= 0:
        return "一贫如洗"
    if cr <= 9:
        return "贫穷"
    if cr <= 49:
        return "普通"
    if cr <= 89:
        return "富裕"
    if cr <= 98:
        return "富有"
    return "巨富"


def derive_assets(cr: int) -> dict:
    """信用评级 → 消费水平 / 现金 / 资产（7 版标准表，1920s 美元）。

    与前端 `apps/web/src/components/character/useCocData.ts` 的 `deriveAssets` 是同一张表，
    两边各有一份是因为编辑器里的「按信用评级换算」按钮要即时出数、不值得为一张查找表走
    一次往返。**改一边必须改另一边**：两侧都有把具体数值钉死的用例（本仓 tests 与
    useCocData.test.ts），任一侧漂移都会红。

    现金那一列一度比规则书高一个量级（×2/×20/×50/×100），建出来的调查员一上来就揣着
    十倍的钱；前端已经修过，这里从一开始就按修正后的倍率写。
    """
    tier = asset_tier(cr)
    if cr <= 0:
        return {"tier": tier, "spendingLevel": 0.5, "cash": 0.5, "assets": 0}
    if cr <= 9:
        return {"tier": tier, "spendingLevel": 2, "cash": cr * 1, "assets": cr * 10}
    if cr <= 49:
        return {"tier": tier, "spendingLevel": 10, "cash": cr * 2, "assets": cr * 50}
    if cr <= 89:
        return {"tier": tier, "spendingLevel": 50, "cash": cr * 5, "assets": cr * 500}
    if cr <= 98:
        return {"tier": tier, "spendingLevel": 250, "cash": cr * 20, "assets": cr * 2000}
    return {"tier": tier, "spendingLevel": 5000, "cash": 50000, "assets": 5000000}


def apply_attr_derived_skills(
    skills: dict[str, int], attrs: dict[str, int]
) -> dict[str, int]:
    """确保属性派生技能达到其基础底线：母语≥EDU、闪避≥DEX//2。

    母语/闪避依赖具体角色属性，静态的 COC_DEFAULT_SKILLS 无法预置正确值。
    无论客户端是否自带 skills，都在此兜底，避免出现 0/缺失；取 max 以保留
    玩家在基线之上的合法加点。原地修改并返回 skills。
    """
    skills["母语"] = max(skills.get("母语", 0), attrs.get("EDU", 50))
    skills["闪避"] = max(skills.get("闪避", 0), attrs.get("DEX", 50) // 2)
    return skills


def build_default_skills(attrs: dict[str, int]) -> dict[str, int]:
    """基于属性构建默认技能列表（全部基础技能 + 母语/闪避派生值）"""
    return apply_attr_derived_skills(dict(COC_DEFAULT_SKILLS), attrs)
