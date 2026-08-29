"""引号台词的**说话人归属**：把「这句引号是谁说的」从流式状态机里摘出来。

原先这些启发式是 ``filter_narration_stream`` 体内的一串闭包，读得懂它们的前提是先读懂
那台状态机。可它们其实是纯逻辑：给一段引号前文（或引号后的尾巴），判断说话人是谁。
摘出来之后，「归错人」这类问题可以直接对 ``SpeakerResolver`` 写用例，不必再造一条流。

**归属优先级**（``SpeakerResolver.resolve``）：书写/标识语境 → 留旁白；否则
显式说话前缀 → 承接上一位说话人 → 最近作为主语行动的 NPC（弱信号）。
判不出就留旁白——气泡挂错名字比台词留在旁白更伤沉浸感。

本模块的正则与三个 helper 由 ``narration_protocol`` 原样迁入，行为逐字节不变。
"""

from __future__ import annotations

import re

from app.services import world_memory

# 书写/标识语境：其后引号是书写/标识内容（非台词），留旁白。允许标识名词与引号间夹分隔符。
_WRITTEN_TEXT_RE = re.compile(
    r"(写着|写道|写有|刻着|刻有|记着|记载|标着|印着|贴着|挂着|题写|题着|题为|落款|显示|显现|上书|"
    r"字牌|牌子|招牌|门牌|标牌|标签|标题|铭牌|告示|名为|名叫|写作|条目|卡片|抽出一张|一行字|"
    r"短讯|电讯|报道|头条|标语|新闻|登载|刊载|载有)"
    # 线索/书写内容常带 markdown 标记或换行（如「写着：> **」「记载：\n# 」），
    # 容忍这些标点/标记夹在提示词与引号之间，避免书写内容被误抽成台词。
    r"[：:，,、\s—\-*>＞#`～~。.]*$"
)
# 感知/指称语境：其后引号是被提及/被听到的词语（非台词），留旁白。
_REFERENCE_BEFORE_RE = re.compile(
    r"(听到|听见|听过|想起|想到|提到|提及|讲到|说到|读到|看到|见到|记得|念及|称为|称作|叫做|叫作|唤作|所谓|对于|关于)[：:，,、\s]*$"
)
# 显式说话前缀：行尾「X说道：」「X：」（X 为 2-6 个中文名/称呼），用于把紧邻引号判为台词。
# 不收单字「答」——它几乎只作双字词尾（回答/答话），单收会把「修女在回答」切成「修女在回」+「答」，
# 把动词短语的半截当说话人；真正的答话由「答道/回答」或冒号形式覆盖。
_SAY_PREFIX_RE = re.compile(
    r"([一-龥·]{2,6})(?:说道|说|问道|问|答道|回答|开口道|开口|低声道|低声|喊道|叫道|笑道|沉声道|轻声道|道|：|:)[：:，,]?\s*$"
)
# 无名角色说话前缀的兜底解析：不靠上面的贪婪正则（它会把「面包房老板娘玛莎笑了笑：」
# 截成「娘玛莎笑了笑」）。逐个剥掉句尾说话动词后，从右往左试 2~6 字候选，
# 用 is_plausible_npc_name 与「是否是独立称呼」筛选——护工/老板娘/小姑娘这类
# 模组没写的路人必须归到自己名下，不能落进最近一个模组 NPC 的气泡。
_GENERIC_SPEAK_VERBS = (
    "低声道", "沉声道", "轻声道", "高声道", "冷声道", "低声说", "高声说", "轻声说",
    "开口道", "回答道", "问道", "答道", "笑道", "喊道", "叫道", "笑了笑",
    "说道", "回答", "开口", "说", "道", "问", "答",
)
#: 候选开头不能是这些量词/领属助词——「一个护工」应剥成「护工」而非「个护工」。
_GENERIC_LEAD_REJECT = set("一个这那每某的之位名")
#: 没有明确说话动词、只有冒号时，候选至少得带这些「人味」特征才认；
#: 否则「墙上的四个门：」会被当成一个叫「四个门」的人。
_GENERIC_HUMAN_HINTS = "人男女老小爷叔伯婆姨姑娘先生女士太太夫人小姐护士护工贩匠师员官长"
#: 组合称呼的合法开头（身份词）：有明确说话动词时优先认「老板娘玛莎」而不是只剩「玛莎」。
_GENERIC_ROLE_STARTS = (
    "老板娘", "老板", "护士长", "小姑娘", "面包房", "报摊", "护工", "男人", "女人",
    "陌生人", "店员", "摊主", "店主", "司机", "医生", "警察", "警员", "修女", "牧师",
    "门房", "报童", "老太太", "老先生", "先生", "女士", "太太", "夫人", "小姐",
)
_SPEAK_VERB_ALT = (
    r"(?:低声道|沉声道|轻声道|高声道|冷声道|低声说|高声说|轻声说|说道|说|问道|问|答道|答|"
    r"开口道|开口|喊道|叫道|笑道|笑了笑|道)?"
)
# 闭引号「后面」紧跟的说话动词：用于「台词在前、说话人后置」的写法（如『“……”她说』
# 『“……”她回头对你说』）——这类现有「看引号前文」的判定抽不出说话人，会把台词漏成旁白。
# 只收明确的说话动词、去掉单字「道/问/答」等歧义词，降低把「知道/街道」误判成台词的概率。
_TRAILING_SAY_VERB_RE = re.compile(
    r"(说道|说|问道|喊道|叫道|低声道|开口道|沉声道|轻声道|笑道|叹道|回答道|回道|答道|开口)"
)
# 说话人后置且用代词时的兜底署名（判不出具名 NPC 时，用代词也好过把台词混进旁白）。
_PRONOUN_SPEAKERS = ("她", "他", "它", "您", "咱")



def strip_speaker_prefix(text: str, speaker: str) -> str:
    """抹掉旁白行尾的「<说话人名>[说道]：」前缀（按完整名/局部名删，长名也不残留半截）。"""
    names = [speaker] + [p for p in speaker.split("·") if len(p) >= 2]
    for nm in sorted(names, key=len, reverse=True):
        # 先做「最大前缀」删除：泛称只拿到「老板娘玛莎」，正文写的却是
        # 「面包房老板娘玛莎笑了笑：」——把说话动词前至多 12 个连续中文/
        # 领属助词一并吞掉，避免旁白里残留「面包房」「一个」这类断尾巴。
        # 吞到标点/换行为止，不会越过句界吃掉上一句。
        new = re.sub(
            r"[一-龥的之]{0,12}" + re.escape(nm) + _SPEAK_VERB_ALT + r"[：:，,]?\s*$",
            "", text,
        )
        if new != text:
            return new
        new = re.sub(re.escape(nm) + _SPEAK_VERB_ALT + r"[：:，,]?\s*$", "", text)
        if new != text:
            return new
    return text
# 句首/小句边界后充当「主语·动作」的 NPC 名（如「诺特点点头」「史蒂芬转过身」），用于
# 在说话人以代词「他/她」承接、附近又有玩家名时，仍能把台词归给真正在行动的 NPC。
# 含逗号/分号：并列小句的主语常跟在逗号后（「格雷夫斯走进书房，霍尔护士长跟在身后」），
# 漏识别会让多说话人歧义保护（≥2 主语不猜）失效。
_SUBJECT_BOUNDARY = "。！？!?\n　 ”」』）)】，,；;"
# 名字后紧跟这些助词 → 是所有格/枚举（「科比特的…」「科比特、邓宁」），是被谈论的修饰语，
# 不是「在说话的主语」——「最近 NPC 主语」判定时不计入，避免被提及者被当说话人。
_POSSESSIVE_AFTER = "的之、和与及兼或"

def narr_quote_span(open_q: str, buf: str, close_q: str) -> str:
    """把「没抽成对话气泡、原样留旁白」的引号片段拼回旁白：剥掉紧贴开/闭引号的换行。

    否则 KP 常写的『台词……\\n”』（闭引号另起一行）会让闭引号在旁白里孤立成一行——
    即用户报的「双引号被分到旁白中」。只剥引号首尾贴着的换行，台词内部换行（多行台词）保留。
    """
    return open_q + buf.strip("\n") + close_q


def is_party_speaker(name: str, party_names: set[str] | None) -> bool:
    """说话人是否属于玩家党（玩家 + AI 队友）——KP 绝不能用台词气泡替他们说话/行动。

    容忍全名与名字片段互为子串（「伊芙琳」↔「伊芙琳·哈特」）；宁可偶尔挡下一个名字重叠的
    NPC，也不放过「KP 替玩家发声」——后者是最伤的沉浸感杀手。
    """
    if not party_names:
        return False
    n = (name or "").strip()
    if len(n) < 2:
        return False
    for pn in party_names:
        pn = (pn or "").strip()
        if pn and (n == pn or n in pn or pn in n):
            return True
    return False


class SpeakerResolver:
    """一次生成期间的说话人归属器。

    ``last_speaker`` 是**有状态**的一项：同一段落内后续引号承接当前说话人；
    段落分隔（``\\n\\n``）时由调用方清掉——那是「话筒交还」的时刻。
    """

    def __init__(self, npcs: list[dict] | None, party_names: set[str] | None) -> None:
        self.party_names = party_names
        self.last_speaker: str | None = None
        # (归一名, 可匹配的局部名, 是否玩家方)
        self.matchers: list[tuple[str, list[str], bool]] = []
        for _n in (npcs or []):
            _name = _n.get("name", "")
            if not _name:
                continue
            _parts = [_name]
            for _sep in ("·", "·", " ", "-"):
                if _sep in _name:
                    _parts.extend(p.strip() for p in _name.split(_sep) if len(p.strip()) >= 2)
                    break
            self.matchers.append((_name, _parts, bool(_n.get("is_player"))))

    def is_party(self, name: str | None) -> bool:
        """该署名是否属于玩家党——KP 绝不能用气泡替他们说话。"""
        return is_party_speaker(name or "", self.party_names)

    @staticmethod
    def looks_like_trailing_say(tail: str) -> bool:
        """闭引号后的头几个字是不是「，她说」这类后置说话人引导。"""
        return bool(_TRAILING_SAY_VERB_RE.search(tail[:12]))

    def canon(self, name: str) -> str:
        name = (name or "").strip()
        for canonical, parts, _ in self.matchers:
            if name == canonical or name in parts:
                return canonical
        return name

    def named_in_text(self, speaker: str | None, text: str) -> bool:
        """说话人名字出现在台词内容里 → 多半是「被谈论」而非「在说话」。

        典型：修女谈论科比特（『科比特藏得很深…』），启发式却把台词署名成科比特——
        被谈论者≠说话者。用于压制这类张冠李戴（仅对非显式前缀的弱判定生效）。"""
        if not speaker or not text:
            return False
        for canonical, parts, _ in self.matchers:
            if canonical == speaker:
                return any(p in text for p in parts)
        return speaker in text

    def _known_canonical(self, name: str) -> str | None:
        for canonical, parts, is_player in self.matchers:
            if name == canonical or name in parts or name in canonical:
                return None if is_player else canonical
        return None

    def _generic_prefix_speaker(self, s: str) -> str | None:
        """无名角色说话前缀的右剥离式解析（见 _GENERIC_SPEAK_VERBS 注释）。"""
        base = s.rstrip("：:，, \t")
        verb_matched = False
        # 从最长动词开始、可连续剥离：「低声说」要整个拿掉，不能只剥「说」留下「低声」。
        for verb in sorted(_GENERIC_SPEAK_VERBS, key=len, reverse=True):
            if base.endswith(verb):
                base = base[: -len(verb)].rstrip("：:，, \t")
                verb_matched = True
                break
        if not base:
            return None
        # 第一轮：优先认「老板娘玛莎」「护士长」这类以明确身份词开头的组合称呼；
        # 第二轮：退回最短的可用候选（玛莎/护工/男人）。
        candidates: list[tuple[int, str]] = []
        for length in range(2, 7):
            start = len(base) - length
            if start < 0:
                continue
            candidate = base[start:]
            known = self._known_canonical(candidate)
            if known is not None:
                return known
            if not world_memory.is_plausible_npc_name(candidate):
                continue
            if candidate[0] in _GENERIC_LEAD_REJECT:
                continue
            prev = base[start - 1] if start > 0 else ""
            if not verb_matched:
                # 只有冒号、没有明确说话动词：要求候选要么是小句主语（有边界），
                # 要么带明显「人味」称呼——否则「墙上的四个门：」会被当成说话人。
                if prev not in _SUBJECT_BOUNDARY and not any(
                    mark in candidate for mark in _GENERIC_HUMAN_HINTS
                ):
                    continue
            candidates.append((start, candidate))
        for _start, candidate in candidates:
            if any(candidate.startswith(role) for role in _GENERIC_ROLE_STARTS):
                return candidate
        return candidates[0][1] if candidates else None

    def _prefix_speaker(self, s: str) -> str | None:
        """行尾「X说道：」「X：」→ 说话人（命中已知 NPC 局部名则归一；玩家方角色返回 None 抑制）。"""
        m = _SAY_PREFIX_RE.search(s)
        if m:
            known = self._known_canonical(m.group(1))
            if known is not None:
                return known
            # 旧正则没认出的（长称呼/泛称/「笑了笑」）交给右剥离式兜底；
            # 兜底会重新做代词、边界与合理性校验，因此这里不再提前 return None。
        return self._generic_prefix_speaker(s)

    def _recent_npc_subject(self, s: str) -> str | None:
        """最近作为「小句主语」出现的非玩家 NPC（名字紧跟在句首/句末标点后）→ 其后台词的说话人。

        窗口内出现 **≥2 个不同 NPC 主语**时返回 None：此时「取最近者」≈瞎猜（约一半会归错，
        气泡挂错名字比台词留在旁白更伤沉浸感），宁可留旁白——多说话人场景由 KP 的 [SAY]
        显式指定（prompt 已强制），不靠启发式赌。"""
        recent = s[-200:]
        best_pos, best = -1, None
        subjects: set[str] = set()
        for canonical, parts, is_player in self.matchers:
            if is_player:
                continue
            for part in parts:
                start = 0
                while True:
                    p = recent.find(part, start)
                    if p < 0:
                        break
                    after = recent[p + len(part): p + len(part) + 1]
                    if p == 0 or recent[p - 1] in _SUBJECT_BOUNDARY:
                        # 计入 subjects（多 NPC 在场 → 触发「≥2 不猜」保护，宁可留旁白）；
                        # 但名字后紧跟所有格/枚举助词（「科比特的遗嘱执行人」「科比特、邓宁」）时是
                        # 被谈论的修饰语/列举，不是「在说话的主语」——不作为返回的说话人。
                        subjects.add(canonical)
                        if after not in _POSSESSIVE_AFTER and p > best_pos:
                            best_pos, best = p, canonical
                    start = p + 1
        if len(subjects) >= 2:
            return None
        return best

    def resolve(self, pre: str) -> tuple[str | None, bool, bool, bool]:
        """返回 (说话人, 是否弱信号, 是否来自显式前缀, 是否书写内容)。弱信号（仅靠最近 NPC
        主语推断）下，仅当引号文本「像台词」才抽取，避免把门牌/招牌等短名词标签误判为台词。
        from_prefix=True 时，调用方需把「X：」前缀从旁白里抹掉，免得说话人名重复显示。
        is_written=True 表示该引号是书写/标识内容（门牌、招牌、刻字…），留旁白。"""
        s = pre.rstrip()
        if _WRITTEN_TEXT_RE.search(s) or _REFERENCE_BEFORE_RE.search(s):
            return None, False, False, True   # 书写/标识/被提及 → 留旁白
        spk = self._prefix_speaker(s)
        if spk:
            return spk, False, True, False    # 强：显式说话前缀
        if self.last_speaker:
            return self.last_speaker, False, False, False  # 强：承接当前说话人（段落分隔后会被释放）
        return self._recent_npc_subject(s), True, False, False  # 弱：最近行动的 NPC 主语

    def trailing_speaker(self, tail: str, narration: str) -> str | None:
        """从闭引号后的文字（如「，她说」「霍尔护士长低声道」）解析后置说话人：具名优先，
        其次承接 last_speaker / 最近 NPC 主语，最后兜底用代词本身。判不出返回 None。"""
        seg = tail.lstrip("，,。、：: 　\t")
        for canonical, parts, is_player in self.matchers:
            if is_player:
                continue
            for part in parts:
                if seg.startswith(part):
                    return canonical
        spk = self.last_speaker or self._recent_npc_subject(narration)
        if spk:
            return spk
        return seg[0] if seg and seg[0] in _PRONOUN_SPEAKERS else None
