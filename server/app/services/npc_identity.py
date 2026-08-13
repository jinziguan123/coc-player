"""NPC 的「对外称呼」：玩家还没认出来的东西，机制界面不能替 KP 把名字说出来。

模组里的 NPC 名同时充当两种角色——KP 侧的唯一标识，和玩家看到的显示名。
对普通 NPC 无所谓，对怪物就是剧透：叙事里 KP 好好地写着「一团比夜色更浓的黑…
没有脸，没有眼」，对抗检定卡却在旁边印出「田间潜随者（莎布·尼古拉丝化身）」，
玩家一眼就知道自己在跟哪尊旧日支配者的化身打交道，悬念当场作废。

**判据是 KP 自己的叙事**：玩家正是从叙事里认识这些东西的。KP 在公开叙事/对白里
写过这个名字 → 玩家已经知道 → 机制界面照常显示；没写过 → 显示中性称呼。
不需要额外标注，也不给 KP 加负担，而且自动跟上剧情：等玩家读到典籍、
NPC 说破身份、KP 在叙事里直呼其名的那一刻，机制界面自然跟着改口。

**外加一条开局判据**：模组的 ``player_brief``。光看本局叙事的话，开场第一句话之前
prose 是空的，模组里每个 NPC 一律先按陌生人处理——《鬼屋》的调查员分明是诺特请来的，
气泡上却写着「陌生男性」。player_brief 的定义（见 module_service）就是「玩家角色开场
本就合法知道的前情：身份动机、受谁委托、为何来到起始地点」，委托人、雇主、同行的同事
必然写在里面，而需要玩家自己发现的东西一概不许写进去——正好就是这里要的那份名单。

模组里的名字普遍写成「外号（神话身份）」这种形式（田间潜随者（莎布·尼古拉丝化身）、
呼子（蠕虫行者）），括号里那层本就是给 KP 看的，所以揭示分三级：
神话身份出现过 → 全名；只有外号出现过 → 只给外号；都没出现 → 中性称呼。
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from sqlalchemy.orm import Session

from app.models.module import Module
from app.services import session_service

#: 玩家还认不出这东西时的兜底称呼。看着不像人的（怪物、神话生物）用它。
#: 模组可以给单个 NPC 写 ``unknown_as`` 覆盖成更贴切的（如「树影里的东西」）。
UNKNOWN_LABEL = "不明存在"

#: 看着是人、但玩家还不知道名字时的称呼。管它是不是真的人——
#: 半幽灵香澄澪在玩家眼里就是个穿学生服的青年，遮名遮的是名字，不是外观。
UNKNOWN_HUMAN = {"male": "陌生男性", "female": "陌生女性", "": "陌生人"}

#: 外观上的性别线索，**按这个顺序**逐个查，先具体后笼统。顺序是有讲究的：
#: 「黄婆干儿子」里 `儿子` 必须排在 `婆` 前面，否则这位男警员会被判成女性——
#: 光看关键词在不在，语义是会反过来的。只认明写的，绝不从名字猜。
_GENDER_PATTERNS: tuple[tuple[str, str], ...] = (
    # 亲属称谓说的是这个 NPC 自己是谁（「维托里奥的妻子」＝她是妻子）
    ("女儿", "female"), ("妻子", "female"), ("母亲", "female"), ("姐姐", "female"),
    ("妹妹", "female"), ("孙女", "female"), ("侄女", "female"),
    ("儿子", "male"), ("丈夫", "male"), ("父亲", "male"), ("哥哥", "male"),
    ("弟弟", "male"), ("孙子", "male"), ("侄子", "male"),
    # 明写的性别
    ("女性", "female"), ("女子", "female"), ("女士", "female"), ("妇女", "female"),
    ("少女", "female"), ("姑娘", "female"), ("夫人", "female"), ("太太", "female"),
    ("小姐", "female"), ("女警", "female"), ("女学生", "female"), ("女人", "female"),
    ("男性", "male"), ("男子", "male"), ("男人", "male"), ("先生", "male"),
    ("少年", "male"), ("老头", "male"), ("大爷", "male"), ("小伙", "male"),
    ("汉子", "male"), ("男孩", "male"), ("男警", "male"),
    # 兜底的单字（放最后，前面都没命中才轮到）
    ("婆", "female"), ("嫂", "female"), ("姨", "female"), ("奶奶", "female"),
    ("大娘", "female"), ("妇", "female"), ("女", "female"),
    ("叔", "male"), ("伯", "male"), ("爷", "male"), ("男", "male"),
)

#: 一眼看出不是人的。先查它，命中就是「不明存在」，不再往下判性别——
#: 「体型庞大的男子…生有巨大金狼头」要是先判了性别，就成了「陌生男性」。
_INHUMAN_HINTS = (
    "神话生物", "怪物", "化身", "触手", "触须", "野兽", "巨兽", "半人半", "人形怪",
    "鳞", "蹼", "菌", "黏液", "肉块", "腐烂", "脓", "狼头", "兽头", "独眼",
    "枯树", "巨树", "活尸", "不死", "干枯如", "不可名状", "无法形容", "数不清",
)
#: 看着像人的。上面没否掉、这里命中，才算「人形」。放得宽一些——
#: 把人误判成「不明存在」只是别扭，把怪物说成「陌生男性」才是砸场子，
#: 而怪物那边已经由上面的否决词兜住了。
_HUMAN_HINTS = (
    "人", "男", "女", "青年", "少年", "老人", "孩子", "学生", "先生", "士",
    "岁", "打扮", "衣", "服", "帽", "面孔", "脸", "皮肤", "头发", "身材", "身影",
    "员", "官", "长", "师", "工", "商", "贩", "医", "警", "兵", "者", "家", "主",
    "邻", "守", "客", "友", "徒", "伙",
)

#: 名字里的敬称也算明写的性别（「佐利先生」）。只认这类词组，不认单字——
#: 名字里出现一个「男」「女」字什么也说明不了，从名字猜性别是要出事的。
_NAME_HONORIFICS: tuple[tuple[str, str], ...] = (
    ("女士", "female"), ("夫人", "female"), ("太太", "female"), ("小姐", "female"),
    ("嬷嬷", "female"), ("婆", "female"), ("大娘", "female"), ("大婶", "female"),
    ("先生", "male"), ("大爷", "male"), ("老爷", "male"), ("少爷", "male"),
)


def unknown_label(npc: dict) -> str:
    """玩家还不知道名字时，界面上怎么称呼这个 NPC。

    优先级：模组明写的 ``unknown_as`` → 明写的 ``looks_human``/``gender``
    → 从 ``description`` 上的外观线索推断 → 兜底「不明存在」。

    判据只取 ``description``（玩家看得到的样子），不碰 ``background``/``secrets``
    ——那些是「它究竟是什么」，而这里要答的是「玩家眼里它是什么」。香澄澪其实是
    半幽灵，但玩家看到的就是个穿学生服的青年；反过来，田间潜随者的 secrets 里
    用「她」称呼它，那不代表玩家该看到「陌生女性」。
    """
    explicit = str(npc.get("unknown_as") or "").strip()
    if explicit:
        return explicit

    looks_human = npc.get("looks_human")
    gender = str(npc.get("gender") or "").strip().lower()
    if gender not in ("male", "female"):
        gender = ""

    desc = str(npc.get("description") or "")
    if looks_human is None:
        if any(h in desc for h in _INHUMAN_HINTS):
            looks_human = False
        else:
            looks_human = any(h in desc for h in _HUMAN_HINTS)
    if not looks_human:
        return UNKNOWN_LABEL

    if not gender:
        name = str(npc.get("name") or "")
        gender = (
            next((g for word, g in _GENDER_PATTERNS if word in desc), "")
            or next((g for word, g in _NAME_HONORIFICS if word in name), "")
        )
    return UNKNOWN_HUMAN[gender]

#: 只有这两类事件算「玩家从中认识了这个名字」：KP 写的公开叙事与对白。
#: 刻意不含 dice/system/action——机制事件的名字正是本模块要遮的东西，
#: 拿它当揭示依据会自我实现：卡上印一次，就永远算「玩家已知」了。
_PROSE_EVENT_TYPES = ("narration", "dialogue")

_PAREN = re.compile(r"[（(]([^（()）]+)[)）]\s*$")


def split_name(name: str) -> tuple[str, str]:
    """把「外号（神话身份）」拆成 (外号, 神话身份)；没有括号时后者为空。"""
    text = (name or "").strip()
    m = _PAREN.search(text)
    if not m:
        return text, ""
    return text[: m.start()].strip(), m.group(1).strip()


#: 一个字的别名不收。中文里单字（「金·戴伯伦」的「金」）随便一句话都撞得上，
#: 收进来等于不遮。这不是在推断语义，是数据卫生。
_MIN_ALIAS_LEN = 2


def call_names(npc: dict) -> list[str]:
    """场上可能怎么称呼这个 NPC：档案全名（去掉神话身份那层）+ 模组给的别名。

    **为什么需要别名。** 档案里存的是全名「史蒂芬·诺特」，而 KP 和玩家在场上永远说
    「诺特先生」——中文音译名是「名·姓」，日常称呼用「姓+敬称」。只拿全名做子串匹配，
    「诺特先生」里并不含「史蒂芬·诺特」，于是这位委托人整局都是「陌生男性」。

    别名由模组导入时生成（那时 LLM 看得到整本模组，分得清「科比特老宅」和沃尔特本人），
    不在运行时猜——运行时无论怎么拆姓名都分不清这两者。
    """
    base, _epithet = split_name(str(npc.get("name") or "").strip())
    names = [base] if base else []
    for raw in npc.get("aliases") or []:
        alias = str(raw or "").strip()
        if len(alias) >= _MIN_ALIAS_LEN and alias not in names:
            names.append(alias)
    return names


def _disambiguate(npcs: dict[str, dict]) -> dict[str, tuple[str, ...]]:
    """算出每个 NPC **专属于他**的那些称呼：指认不到唯一一个人的别名一律不作数。

    马卡里奥一家三口共姓，「前租户马卡里奥一家搬走后」这句话谁也不该解锁——此刻这个
    称呼指不到具体某个人。这不是又一条启发式，是逻辑上的必然：歧义的称呼不能当识别依据。

    两种歧义都要挡，缺一不可：

    1. **多个 NPC 都列了这个别名**（三口人的 aliases 里都有「马卡里奥」）。
    2. **它是另一个 NPC 全名的一部分**（只有特蕾莎列了「马卡里奥」，另两位没列）。
       导入期的裁决不保证在一家人身上前后一致，实测就出现过这种一漏两中；只查第 1 条的话
       漏网的那个反而成了「独占」别名，一句「马卡里奥一家」就把活尸小女孩的身份解锁了。

    档案全名无条件保留（它是这个 NPC 的规范名，KP 写全名就是点名道姓），只有别名参与消歧。
    """
    owners: dict[str, set[str]] = {}
    for name, npc in npcs.items():
        for call in call_names(npc):
            owners.setdefault(call, set()).add(name)
    bases = {name: split_name(name)[0] for name in npcs}
    out: dict[str, tuple[str, ...]] = {}
    for name, npc in npcs.items():
        base = bases[name]
        others = [b for other, b in bases.items() if other != name]
        out[name] = tuple(
            c for c in call_names(npc)
            if c == base or (
                len(owners.get(c, ())) == 1 and not any(c in b for b in others)
            )
        )
    return out


class NameMasker:
    """按「这局玩家看过的叙事」把 NPC 名换成对外称呼。

    一局里要遮的名字往往不止一个（战斗一轮就要过一遍先攻表），所以做成
    一次构建、多次调用：叙事正文只拼一次。
    """

    def __init__(self, prose: str, npcs: Iterable[dict] | None = None):
        self._prose = prose or ""
        self._npcs: dict[str, dict] = {}
        for npc in npcs or []:
            name = str(npc.get("name") or "").strip()
            if name:
                self._npcs[name] = npc
        self._calls = _disambiguate(self._npcs)

    def _resolve(self, name: str, npc: dict, prose: str) -> str:
        base, epithet = split_name(name)
        # 知道了神话身份，再遮外号没有意义——全名照给
        if epithet and epithet in prose:
            return name
        # 任一称呼在叙事里出现过，就算玩家认得这个人了。显示仍用档案里的 base，
        # 免得同一个人一会儿「诺特先生」一会儿「史蒂芬·诺特」地跳。
        if base and any(c in prose for c in self._calls.get(name, (base,))):
            return base
        return unknown_label(npc)

    def __call__(self, name: str | None, extra_prose: str = "") -> str:
        """取对外称呼。不是本模组 NPC 的名字（玩家角色、临场 NPC）原样返回。

        ``extra_prose`` 给「这一条还没落库的叙事/台词」用：NPC 自报家门那一刻
        （「我叫香澄澪」）就该同时改口，否则气泡会是「不明存在：我叫香澄澪」。
        """
        text = (name or "").strip()
        npc = self._npcs.get(text)
        if npc is None:
            return text
        prose = f"{self._prose}\n{extra_prose}" if extra_prose else self._prose
        return self._resolve(text, npc, prose)

    def reveals(self, name: str | None) -> bool:
        """这个名字现在是不是照实显示（供调用方判断要不要额外藏细节）。"""
        text = (name or "").strip()
        return self(text) == text


def _player_visible_prose(db: Session, session_id: str) -> str:
    """本局玩家看得到的 KP 叙事与对白正文。

    「仅 KP 可见」的事件（幕后推演等）不算数——玩家没看过的东西不能算他知道。
    """
    parts: list[str] = []
    for ev in session_service.get_session_events(db, session_id):
        if ev.event_type not in _PROSE_EVENT_TYPES:
            continue
        if session_service.is_kp_only_event(ev):
            continue
        parts.append(ev.content or "")
    return "\n".join(parts)


def _pre_known_prose(module: Module | None) -> str:
    """模组写明的「玩家开场就已经知道的前情」，与本局叙事同等对待。

    只取 ``player_brief``，不取 ``intro``：后者是世界观与基调的铺陈，里面出现的名字
    （地名、传说人物）不代表调查员认识本人。
    """
    ws = getattr(module, "world_setting", None) if module is not None else None
    if not isinstance(ws, dict):
        return ""
    return str(ws.get("player_brief") or "").strip()


def build_masker(db: Session, session_id: str, module: Module | None) -> NameMasker:
    """按当前进度构建一个对外称呼表。"""
    npcs = list(getattr(module, "npcs", None) or []) if module is not None else []
    npcs = [n for n in npcs if isinstance(n, dict)]
    if not npcs:
        return NameMasker("", [])
    prose = _player_visible_prose(db, session_id)
    known = _pre_known_prose(module)
    return NameMasker(f"{known}\n{prose}" if known else prose, npcs)


def public_name(
    db: Session, session_id: str, module: Module | None, name: str | None,
) -> str:
    """单个名字的对外称呼。要遮多个名字时用 :func:`build_masker`，别重复扫事件。"""
    return build_masker(db, session_id, module)(name)
