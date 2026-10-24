"""移动原话的确定性解释；不读取模组、存档或模型服务。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ── 原话信号 ─────────────────────────────────────────────────────────

SENTENCE_SPLIT = re.compile(r"[。！!；;\n]+")
_QUESTION = ("?", "？")
_CONDITIONAL = ("如果", "假如", "要是", "若是", "倘若", "万一", "一旦", "只要", "除非", "等到")
#: 句中任何位置出现都表示「商量」。
_DISCUSS_ANYWHERE = (
    "要不要", "是否", "能不能", "可不可以", "可以吗", "去不去", "进不进", "要去吗",
    "建议", "提议", "再决定", "再说", "看看吧", "吧",
)
#: 只在移动动词**之前**出现才算商量：「我们之后可能去B」是打算，「我钻进去之后打开手电」是行动。
_DISCUSS_BEFORE_VERB = (
    "之后", "以后", "回头", "稍后", "待会", "等会", "过会儿", "晚点", "晚些", "过一阵",
    "可能", "也许", "或许", "打算", "准备", "计划", "想去", "想进", "希望", "考虑", "应该", "不如",
)
_NEGATED = (
    "不进", "别进", "不要进", "不想进", "不敢进", "没进", "别钻", "不钻", "先别", "别急",
    "不过去", "别过去", "不去", "别去", "不走", "不能进", "进不去", "过不去", "钻不进",
)
#: 穿过出口的动作。只认这些才算「经某出口离开」；「走近/走向/来到…前」是靠近，不是穿过。
_TRAVERSE = (
    "钻进", "钻入", "钻过", "钻出", "穿过", "穿进", "走进", "走入", "进入", "踏入", "踏进",
    "迈进", "迈入", "跨进", "跨入", "跨过", "爬进", "爬入", "爬过", "爬出", "挤进", "挤过",
    "溜进", "闯进", "闯入", "冲进", "冲入", "跳进", "跳入", "走出", "退出", "退回", "回到",
    "返回", "进去", "出去", "通过", "离开", "走下", "爬下",
)
#: 点名目的地时额外认的去往动词（「我去七号车厢」）。
_GO_TO = ("前往", "去往", "前去", "赶往", "抵达", "到达", "来到", "走向", "移动到", "去")
_FOLLOW = ("跟上", "跟着", "跟进", "跟在", "随后", "紧随", "我也去", "我也进", "我也钻")
_APPROACH = (
    "走近", "靠近", "凑近", "凑到", "走到", "挪到", "站到", "蹲到", "走向", "来到", "观察",
    "查看", "打量", "端详", "看看", "照向", "照进", "探头", "往里看", "往里照",
)
_SOLO = (
    "独自", "一个人", "单独", "抛下", "丢下", "撇下", "甩下", "甩开", "不等", "不管你们",
    "你们留", "你们在这", "你们先在", "你们等", "自己先", "我先", "独行", "只身", "不跟你们",
    "别跟着我", "别跟来",
)
#: 叫人等一等、先别动——说这话的人自己显然还没打算走（「慢着，先把灯探进去照一照」）。
_HESITATE = ("慢着", "等一下", "等等", "且慢", "稍等", "别急", "先别", "先看看", "先照", "先把灯")
_COLLECTIVE = ("我们", "咱们", "大家", "一起", "一同", "全员", "所有人", "都跟", "跟我", "随我")

#: 出口名词，按类归并：玩家说「缺口」、描述写「墙洞」，指的是同一类东西。
_EXIT_CLASSES: dict[str, tuple[str, ...]] = {
    "门": ("拱门", "门洞", "门口", "大门", "石门", "木门", "铁门", "暗门", "房门", "侧门", "后门",
          "前门", "舱门", "入口", "出口", "门"),
    "洞": ("墙洞", "洞口", "缺口", "破洞", "窟窿", "裂口", "豁口", "地洞", "洞穴", "洞"),
    "道": ("通道", "过道", "走廊", "甬道", "隧道", "走道", "暗道", "密道", "廊道"),
    "梯": ("楼梯", "台阶", "阶梯", "石阶", "梯子", "绳梯", "竖井"),
    "窗": ("窗户", "窗口", "窗"),
}
_EXIT_WORDS = sorted(
    ((w, cls) for cls, words in _EXIT_CLASSES.items() for w in words),
    key=lambda pair: -len(pair[0]),
)
EXIT_WORD_SET = {w for w, _ in _EXIT_WORDS}


def has_any(text: str, words) -> bool:
    return any(w in text for w in words)


def _first_pos(text: str, words) -> int:
    hits = [text.find(w) for w in words if w in text]
    return min(hits) if hits else -1


def exit_words(text: str) -> list[tuple[str, str]]:
    """文本里的出口名词（最长匹配，不重叠）→ [(原词, 类)]。"""
    found: list[tuple[str, str]] = []
    taken = [False] * len(text or "")
    for word, cls in _EXIT_WORDS:
        start = 0
        while (pos := text.find(word, start)) >= 0:
            span = range(pos, pos + len(word))
            if not any(taken[i] for i in span):
                for i in span:
                    taken[i] = True
                found.append((word, cls))
            start = pos + 1
    return found


@dataclass
class Statement:
    """一个角色本轮原话里的移动信号。"""

    kind: str = "none"   # act / follow / discuss / conditional / negated / approach / none
    sentences: list[str] = field(default_factory=list)   # 判为行动的那几句
    solo: bool = False
    collective: bool = False
    hesitant: bool = False
    travel_scene: str = ""

    @property
    def acts(self) -> bool:
        return self.kind in ("act", "follow")


#: 非行动类别的优先级：一句话里同时有多种信号时，取最能说明「人没动」的那一种。
_KIND_RANK = {"conditional": 5, "discuss": 4, "negated": 3, "approach": 2, "none": 0}


#: 分句：同一句里「如果门打开，我就进去」的条件要整句看，其余信号按逗号/破折号分句看——
#: 「钻进这间屋子后蹲下，看看能不能容下一件东西」里的「能不能」不该把整句行动判成商量。
_CLAUSE_SPLIT = re.compile(r"[，,、]|——|—|…+")
_FIRST_PERSON = ("我", "咱")
#: 原地不动：「只能在原地等待」「我守在洞口」。
_STAY = ("在原地", "留在", "待在", "守在", "原地等", "等在")
#: 「进去/出去」只是趋向补语：前面得是挪动身体的动作或主语/副词才算人在走
#: （「我进去」「钻进去」算；「扫出去」「打进去」「探一条腿进去」不算）。
_BARE_DIRECTIONALS = ("进去", "出去")
_BODY_LEAD = set("钻走爬跨挤溜闯冲跳跑迈踏摸挪跟就也直再先我们身") | set("门洞口道梯窗里")
_NEGATION_LEAD = ("别", "不", "没", "勿", "莫", "甭")
#: 第三人称主语：「凿它的人进去了」说的是别人，不是说话人自己要走。
_THIRD_PERSON = ("他", "她", "它", "有人", "的人", "那人", "别人", "对方", "人家")


def _verb_pos(clause: str, verbs) -> tuple[int, int]:
    """第一个「人在挪动」的动词：(位置, 长度)，没有为 (-1, 0)。趋向补语按 ``_BODY_LEAD`` 过滤。"""
    hits = []
    for verb in verbs:
        start = 0
        while (pos := clause.find(verb, start)) >= 0:
            start = pos + 1
            if verb in _BARE_DIRECTIONALS and pos > 0 and clause[pos - 1] not in _BODY_LEAD:
                continue
            hits.append((pos, len(verb)))
            break
    return min(hits) if hits else (-1, 0)


def _clause_kind(clause: str, named_terms: set[str]) -> str:
    c = clause.strip()
    if not c:
        return "none"
    if has_any(c, _NEGATED):
        return "negated"
    named = bool(named_terms) and has_any(c, named_terms)
    verb_pos, verb_len = _verb_pos(c, _TRAVERSE + (_GO_TO if named else ()))
    if has_any(c, _DISCUSS_ANYWHERE):
        return "discuss"
    if verb_pos >= 0:
        head = c[:verb_pos]
        if head.endswith(_NEGATION_LEAD) or head.endswith(("不要", "不能", "不会", "别再")):
            return "negated"   # 「火别离开洞口」「别离开灯光半步」
        if has_any(head, _DISCUSS_BEFORE_VERB):
            return "discuss"
        third, first = _first_pos(head, _THIRD_PERSON), _first_pos(c, _FIRST_PERSON)
        if third >= 0 and (first < 0 or third < first):
            return "none"   # 在讲别人怎么走（「有人在我们之前进去过」「凿它的人进去了」）
        after = c[verb_pos + verb_len:]
        if after.startswith(("过", "去过", "来过")) or ("是" in head and c.endswith("的")):
            return "none"   # 经历/判断句（「进去过」「是从这一面打进去的」），不是此刻的行动
        return "act"
    if has_any(c, _STAY):
        return "negated"
    if has_any(c, _FOLLOW):
        return "follow"
    if has_any(c, _APPROACH):
        return "approach"
    return "none"


def _sentence_kinds(sentence: str, named_terms: set[str]) -> list[tuple[str, str]]:
    """一句话 → [(分句, 类别)]。问句、条件句整句定性；否则逐分句判。"""
    s = sentence.strip()
    if not s:
        return []
    if has_any(s, _QUESTION):
        return [(s, "discuss")]
    if has_any(s, _CONDITIONAL):
        return [(s, "conditional")]
    return [(c.strip(), _clause_kind(c, named_terms)) for c in _CLAUSE_SPLIT.split(s) if c.strip()]


def analyze(text: str, named_terms: set[str] | None = None) -> Statement:
    """把一个角色本轮的原话归类。逐句、逐分句判：任何一处是自己的行动宣言就算行动
    （「我钻进去。你们要不要跟上？」）；否则取最能说明「人没动」的那类（条件 > 商量 > 拒绝 > 走近）。"""
    stmt = Statement()
    text = (text or "").strip()
    if not text:
        return stmt
    best = "none"
    for sentence in SENTENCE_SPLIT.split(text):
        for clause, kind in _sentence_kinds(sentence, named_terms or set()):
            if kind in ("act", "follow"):
                stmt.sentences.append(clause)
                if stmt.kind != "act":
                    stmt.kind = kind
            elif _KIND_RANK.get(kind, 0) > _KIND_RANK.get(best, 0):
                best = kind
    if not stmt.acts:
        stmt.kind = best
    acted = "".join(stmt.sentences)
    stmt.solo = has_any(text, _SOLO)
    stmt.hesitant = has_any(text, _HESITATE)
    stmt.collective = (
        stmt.acts and not stmt.solo and "分头" not in text and has_any(acted, _COLLECTIVE)
    )
    return stmt


