"""KP 流式叙事的**字符级状态机**：把模型吐出的文本切成「旁白 / NPC 气泡」，并剔除指令标签。

原先这是 ``filter_narration_stream`` 体内一个 500 余行的 for-循环，圈复杂度上百。
它一直**就是**一台状态机，只是没有被承认——五个互斥状态用五个布尔量表示，
状态迁移散落在一串 if/elif 里，改任何一条分支都得先在脑子里把整台机器重建一遍。

这里把它显式化：每个状态一个 ``_feed_*`` 方法，迁移写在方法里，
共享的「提交旁白 / 产出气泡」收敛成少数几个 helper。行为逐字节不变
（``tests/test_narration_protocol_golden.py`` 钉住）。

**状态**（互斥，判定优先级自上而下）：

===========  ==========================================================
DEFERRING    闭引号后判不出说话人、内容又像台词：扣住不落旁白，等「她说」这类后置引导
SAY          在 ``[SAY: who=X]…[/SAY]`` 之内
BRACKET      在 ``[…]`` / ``【…】`` 之内
QUOTE        在 ``“…”`` / ``「…」`` / ``『…』`` 之内
NARRATION    默认态，字符累进 ``pending`` 等待按段/超长切分
===========  ==========================================================
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from typing import Any

from app.ai.text_guard import comparable_passages, is_near_duplicate, normalize_comparison_text
from app.services import command_protocol, narration_speakers
from app.services.event_protocol import make_chunk
from app.services.narration_speakers import narr_quote_span

logger = logging.getLogger(__name__)

# 暗投/暗骰的裁定结果本应「仅 KP 可见」，但模型偶尔会把它错写进方括号泄漏给玩家
# （如 `[暗投结束 - X·心理学检定 失败]`、`【暗骰·NPC·潜行 成功】`）。这类元信息括号一律
# 丢弃、绝不回吐进旁白。匹配：含「暗投/暗骰」，或「检定」紧邻成败判词（含大成功/大失败）。
_BLIND_LEAK_RE = re.compile(
    r"暗投|暗骰|检定[^\[\]【】]{0,8}(大成功|大失败|成功|失败)"
    r"|(大成功|大失败|成功|失败)[^\[\]【】]{0,4}检定"
)

# 相邻引号成串的分隔符（门牌列表「“301”、“302”」）
_LIST_SEPS = " 　\t\n、,，;；/和与及·"

# 超长旁白强制切分的阈值与可切位置
_PENDING_FLUSH_CHARS = 150
_SENTENCE_ENDS = "\n。！？"

# 旁白去重口径：够长的段落才参与近似比对（短句重复是正常修辞）
_DEDUP_MIN_CHARS = 24
_DEDUP_THRESHOLD = 0.86
# 已展示台词的比对口径：短得多（气泡本就短），阈值更严
_SHOWN_MIN_CHARS = 6
_SHOWN_THRESHOLD = 0.90




def looks_like_speech(text: str) -> bool:
    """像台词：有句末标点 / 口语标记 / 够长——用于过滤门牌、招牌等短名词标签。"""
    if len(text) >= 10:
        return True
    return (text and text[-1] in "。！？…?!.~～") or any(
        c in text for c in "你我吗呢吧啊呀嘛！？!?，,"
    )

class NarrationScanner:
    """一次 KP 生成的叙事扫描器。逐字符 ``feed``，产出待广播的 chunk。

    调用方负责：喂 token（并自行累计 ``full_response``）、在 ``terminated`` 置位后停流、
    在每个 token 边界调 ``flush_boundaries``、流末调 ``finish``、最后调 ``write_result``。
    """

    def __init__(
        self,
        result: list,
        npcs: list[dict] | None = None,
        group_label: str | None = None,
        guess_speakers: bool = True,
        party_names: set[str] | None = None,
        shown_dialogues: list[str] | None = None,
        prior_narration: str = "",
    ) -> None:
        self.result = result
        self.group_label = group_label
        self.guess_speakers = guess_speakers
        self.speakers = narration_speakers.SpeakerResolver(npcs, party_names)

        self.narration = ""
        self.pending = ""
        self.terminated = False          # 命中命令标签，本次流就此终止

        # —— BRACKET 态 ——
        self._in_bracket = False
        self._bracket_buf = ""
        self._bracket_open = ""          # 本次括号是 [ 还是【，非指令时按原样回吐

        # —— SAY 态 ——
        self._in_say = False
        self._say_speaker = ""
        self._say_buf = ""

        # —— QUOTE 态 ——
        self._in_quote = False
        self._quote_open = ""
        self._quote_buf = ""
        self._pending_speaker: str | None = None   # 本引号判定出的说话人（None=留旁白）
        self._pending_weak = False                 # 该说话人是否弱信号（仅靠最近主语推断）
        self._pending_from_prefix = False          # 是否来自显式「X：」前缀（强信号）
        self._quote_written = False                # 本次引号是否书写/标识内容
        self._written_run = False                  # 处于一串书写标识引号中（门牌列表等）
        self._gap_since_quote = ""                 # 上一处闭引号至今的旁白（判断是否相邻成串）

        # —— DEFERRING 态 ——
        self._deferring = False
        self._deferred_open = self._deferred_buf = self._deferred_close = self._deferred_tail = ""

        self._protected = [t for t in (shown_dialogues or []) if (t or "").strip()]
        self._seen_narration = comparable_passages(prior_narration, min_chars=_DEDUP_MIN_CHARS)

        self.extracted = result[2]
        self.dialogue_marks: list = result[3] if len(result) > 3 else []
        self.group_marks: list = result[4] if len(result) > 4 else []
        # 分头行动按组生成：确定性地把整段归入该组（流式 metadata + 落库 group_mark）
        if group_label:
            self.group_marks.append((0, group_label))

    # ── 产出 helper ────────────────────────────────────────────

    def _mk(self, chunk_type: str, content: str = "", **kw) -> Any:
        """带分组标签的 make_chunk：group_label 时给 chunk 附 metadata.group，供前端实时分栏。"""
        if self.group_label:
            md = dict(kw.pop("metadata", None) or {})
            md["group"] = self.group_label
            kw["metadata"] = md
        return make_chunk(chunk_type, content, **kw)

    def _is_shown_dialogue(self, text: str) -> bool:
        return is_near_duplicate(
            text, self._protected, min_chars=_SHOWN_MIN_CHARS, threshold=_SHOWN_THRESHOLD,
        )

    def _commit_one(self, text: str) -> str:
        if not text:
            return ""
        if text.strip() and is_near_duplicate(
            text, self._seen_narration, min_chars=_DEDUP_MIN_CHARS, threshold=_DEDUP_THRESHOLD,
        ):
            return ""
        self.narration += text
        self.result[0] = self.narration
        if len(normalize_comparison_text(text)) >= _DEDUP_MIN_CHARS:
            self._seen_narration.append(text.strip())
        return text

    def _commit_narration(self, text: str) -> str | None:
        """按段提交旁白；明显重复的后写版本不广播也不进入落库结果。"""
        accepted: list[str] = []
        remaining = text
        while "\n\n" in remaining:
            index = remaining.index("\n\n") + 2
            accepted.append(self._commit_one(remaining[:index]))
            remaining = remaining[index:]
        accepted.append(self._commit_one(remaining))
        output = "".join(accepted)
        return output if output.strip() else None

    def _flush_pending(self) -> Iterator[Any]:
        if not self.pending:
            return
        out = self._commit_narration(self.pending)
        self.pending = ""
        if out:
            yield self._mk("narration", out, actor_name="KP")

    def _emit_dialogue(self, speaker: str, text: str) -> Any:
        """记账并产出一个 NPC 气泡（调用方已做完玩家党/张冠李戴的抑制判断）。"""
        self.speakers.last_speaker = speaker
        self.extracted.append((speaker, text))
        self.dialogue_marks.append((len(self.narration), speaker, text))
        return self._mk("npc_dialogue", text, actor_name=speaker)

    def _emit_say(self) -> Any | None:
        # 气泡本身即「引号」，KP 若在 [SAY] 内又套了引号（[SAY]“台词”[/SAY]）会让气泡显示成
        # 「“台词」——剥掉首尾包裹的引号；台词内部的引号保留。
        text = self._say_buf.strip().strip("“”「」『』\"")
        speaker = self.speakers.canon(self._say_speaker)
        self._in_say = False
        self._say_buf = ""
        self._say_speaker = ""
        if text and speaker:
            if self.speakers.is_party(speaker):
                return None  # 绝不用气泡替玩家/队友说话：KP 误用 [SAY] 代言 → 丢弃该气泡
            return self._emit_dialogue(speaker, text)
        return None

    # ── 各状态的字符处理 ────────────────────────────────────────

    def feed(self, ch: str) -> Iterator[Any]:
        """吃一个字符，按当前状态分派。命中命令标签时置 ``terminated`` 并立即返回。"""
        if self._deferring:
            yield from self._feed_deferring(ch)
        elif self._in_say:
            yield from self._feed_say(ch)
        elif self._in_bracket:
            yield from self._feed_bracket(ch)
        else:
            yield from self._feed_default(ch)

    def _feed_deferring(self, ch: str) -> Iterator[Any]:
        """等后置说话人：紧跟其后是「她说」这类引导则抽气泡，否则原样归还旁白。"""
        self._deferred_tail += ch
        if self.speakers.looks_like_trailing_say(self._deferred_tail):
            speaker = self.speakers.trailing_speaker(self._deferred_tail, self.narration)
            text = self._deferred_buf.strip()
            # 后置说话人同样压制「被台词内容点名」的张冠李戴（后置判定从不来自显式前缀）
            if speaker and self.speakers.named_in_text(speaker, text):
                speaker = None
            if speaker and self.speakers.is_party(speaker):
                speaker = None  # 不替玩家/队友发声
            if speaker:
                yield self._emit_dialogue(speaker, text)
            else:
                self.pending += narr_quote_span(
                    self._deferred_open, self._deferred_buf, self._deferred_close,
                )
            self.pending += self._deferred_tail  # 「她说……」等引导语作旁白
            self._end_deferring()
            return
        if ch == "\n" or len(self._deferred_tail) > 12:
            # 后面不是紧邻的说话动词 → 判定非台词，原样归还旁白
            self.pending += narr_quote_span(
                self._deferred_open, self._deferred_buf, self._deferred_close,
            ) + self._deferred_tail
            self._end_deferring()

    def _end_deferring(self) -> None:
        self._deferring = False
        self._deferred_tail = ""

    def _feed_say(self, ch: str) -> Iterator[Any]:
        """[SAY] 内：遇 [/SAY] 或空行收束成气泡。"""
        self._say_buf += ch
        if self._say_buf.endswith("[/SAY]"):
            self._say_buf = self._say_buf[: -len("[/SAY]")]
        elif self._say_buf.endswith("\n\n"):
            self._say_buf = self._say_buf[:-2]
        else:
            return
        chunk = self._emit_say()
        if chunk:
            yield chunk

    def _feed_bracket(self, ch: str) -> Iterator[Any]:
        """[…] 内：闭合时按标签种类决定剔除 / 进 SAY / 终止本次流 / 丢弃。"""
        if ch not in "]】":
            self._bracket_buf += ch
            return
        inner = self._bracket_buf.strip()
        self._bracket_buf = ""
        self._in_bracket = False

        if inner.startswith(("MOVE:", "MOVE ", "MAP_MARK:", "MAP_MARK ")):
            return          # 内联剔除，不终止本次流
        if inner.startswith(("GROUP:", "GROUP ")):
            # 只剥掉标记本身，**不采纳** KP 自定的分组。分组是确定性状态：分头时
            # 由后端按 party_locations 归并、逐组生成并注入 group_label（见 group_marks
            # 初始化），全队在一起时压根不该有分组。
            #
            # 曾经这里是照单全收的，于是《鬼屋》那局全队都在街区，KP 从历史里几句
            # 「我们分头吧」推断队伍已分开，自行标出一个模组里不存在的「诺特的事务所」组，
            # 整轮只演那一组——同轮另外三人的行动与一次侦查检定再没有下文
            # （系统判定的是单场景，只跑一次生成，没有第二组）。
            return
        if inner.startswith(("SAY:", "SAY ")):
            yield from self._flush_pending()
            rest = inner[len("SAY"):].lstrip(": ").strip()
            kv = command_protocol.parse_tag_kv(rest)
            self._say_speaker = (
                kv.get("who") or kv.get("name") or kv.get("speaker") or rest
            ).strip()
            self._say_buf = ""
            self._in_say = True
            return
        if command_protocol.is_command_tag(inner):
            self.terminated = True
            return
        # 认不出的括号一律丢弃，绝不回吐给玩家。
        #
        # 括号在旁白里没有正当用途：真实语料里 646 条叙事/对白，方括号总共出现过两次，
        # 两次都是模型自创的机器话（`[隐藏检定：未知]`、`[此前回应出现技术问题，现补充
        # 修正后的内容]`）。原先这里把认不出的原样塞回旁白，于是模型编什么，玩家屏幕上
        # 就出现什么。
        #
        # 早先只拦「暗投/暗骰」「检定+成败」这类**已知**的错误写法，模型换个中文措辞
        # 就绕过去了——错误写法是枚举不完的，只能反过来放行白名单。
        if inner:
            logger.warning(
                "KP 旁白里出现认不出的括号，已丢弃%s：%s",
                "（疑似暗投/暗骰结果泄漏）" if _BLIND_LEAK_RE.search(inner) else "",
                inner[:80],
            )

    def _feed_default(self, ch: str) -> Iterator[Any]:
        """默认态：识别括号起始、引号开闭，其余字符累进 pending / quote_buf。"""
        if ch in "[【":
            self._in_bracket = True
            self._bracket_open = ch
            self._bracket_buf = ""
        elif (ch in "“「『") and not self._in_quote:
            yield from self._open_quote(ch)
        elif (ch in "”」』") and self._in_quote:
            yield from self._close_quote(ch)
        elif self._in_quote:
            self._quote_buf += ch
        else:
            self.pending += ch
            self._gap_since_quote += ch
            # 段落分隔＝说话的「话筒」交还：清掉 last_speaker，避免上一位说话人跨段
            # 把后文（如另一场景里读到的报纸短讯）也吸成自己的台词；书写标识串同样中断。
            if self.pending.endswith("\n\n"):
                self.speakers.last_speaker = None
                self._written_run = False

    def _open_quote(self, ch: str) -> Iterator[Any]:
        """开引号：先判说话人（基于引号前文），冲掉旁白，进入引号收集。

        用 narration+pending 作前文：台词常另起一段，此时前文主语（如「诺特」）已被
        flush 进 narration，只看 pending 会漏掉说话人。
        「相邻成串」判断：上一处闭引号至今只有分隔符 → 与上一引号同属一串。
        """
        adjacent = self._gap_since_quote.strip(_LIST_SEPS) == ""
        if not adjacent:
            self._written_run = False

        if not self.guess_speakers:
            # 结构化路径（say() 工具承担对话）：裸引号一律留旁白，绝不启发式猜说话人。
            self._pending_speaker, self._pending_weak, from_prefix = None, False, False
            self._quote_written = True
            self._written_run = True
        elif self._written_run and adjacent:
            # 续接书写标识串（如门牌列表）：整串都按书写内容留旁白，不抽台词。
            self._pending_speaker, self._pending_weak, from_prefix = None, False, False
            self._quote_written = True
        else:
            speaker, weak, from_prefix, is_written = self.speakers.resolve(
                self.narration + self.pending,
            )
            self._pending_speaker, self._pending_weak = speaker, weak
            self._written_run = is_written
            self._quote_written = is_written
        self._pending_from_prefix = from_prefix

        # 经显式前缀（「史蒂芬·诺特：」）判定说话人时，把该前缀从旁白里抹掉——
        # 否则说话人名会既作旁白文字、又作气泡署名，重复显示。
        if self._pending_speaker and from_prefix:
            self.pending = narration_speakers.strip_speaker_prefix(
                self.pending, self._pending_speaker,
            )
        yield from self._flush_pending()
        self._in_quote = True
        self._quote_open = ch
        self._quote_buf = ""

    def _close_quote(self, ch: str) -> Iterator[Any]:
        """闭引号：按说话人判定结果决定抽气泡 / 扣住等后置说话人 / 留旁白。"""
        self._in_quote = False
        self._gap_since_quote = ""        # 闭引号：重置「相邻成串」计数
        text = self._quote_buf.strip()
        speaker = self._pending_speaker

        # 弱信号下要求「像台词」，否则按标签/名词留旁白（门牌、招牌等）
        ok = bool(text and speaker) and (not self._pending_weak or looks_like_speech(text))
        # 非显式前缀判定的说话人若被台词内容点名（修女谈论科比特→署名科比特），压制归属
        if ok and not self._pending_from_prefix and self.speakers.named_in_text(speaker, text):
            ok = False
        if ok and self.speakers.is_party(speaker):
            ok = False  # 不替玩家/队友发声

        if self._is_shown_dialogue(text):
            # 玩家/队友台词已经以气泡展示；KP 再次套引号复述时整段丢弃。
            pass
        elif ok:
            yield self._emit_dialogue(speaker, text)
        elif not self._quote_written and text and looks_like_speech(text):
            # 判不出说话人、但内容像台词：先扣住不落旁白，看紧跟其后的是不是「她说」这类
            # 后置说话人（说话人在台词之后），是则抽成气泡、否则原样归还旁白。
            self._deferring = True
            self._deferred_open, self._deferred_buf, self._deferred_close = (
                self._quote_open, self._quote_buf, ch,
            )
            self._deferred_tail = ""
        else:
            self.pending += narr_quote_span(self._quote_open, self._quote_buf, ch)  # 非台词

        self._quote_buf = ""
        self._pending_speaker = None
        self._pending_weak = False
        self._pending_from_prefix = False

    # ── token 边界与收尾 ────────────────────────────────────────

    def flush_boundaries(self) -> Iterator[Any]:
        """token 边界：把已成段（\\n\\n）或过长的 pending 切出去，让前端尽早看到字。

        只在不处于任何嵌套状态时才切——引号/括号/[SAY] 中途切会把半截标记推给前端。
        """
        if self._in_bracket or self._in_say or self._in_quote or not self.pending:
            return
        while "\n\n" in self.pending:
            idx = self.pending.index("\n\n") + 2
            chunk, self.pending = self.pending[:idx], self.pending[idx:]
            out = self._commit_narration(chunk)
            if out:
                yield self._mk("narration", out, actor_name="KP")
        if len(self.pending) > _PENDING_FLUSH_CHARS:
            last_b = -1
            for i, c in enumerate(self.pending):
                if c in _SENTENCE_ENDS:
                    last_b = i
            if last_b >= 0:
                chunk, self.pending = self.pending[: last_b + 1], self.pending[last_b + 1:]
                out = self._commit_narration(chunk)
                if out:
                    yield self._mk("narration", out, actor_name="KP")

    def flush_on_terminate(self) -> Iterator[Any]:
        """命中命令标签而终止：把已攒下的旁白交出去。"""
        yield from self._flush_pending()

    def finish(self) -> Iterator[Any]:
        """流正常结束：各状态各自归还未闭合的内容，再冲掉 pending。"""
        if self._in_say:
            chunk = self._emit_say()
            if chunk:
                yield chunk
        if self._in_quote:
            if not self._is_shown_dialogue(self._quote_buf):
                # 未闭合引号：留旁白
                self.pending += self._quote_open + self._quote_buf.rstrip("\n")
        if self._in_bracket:
            self.pending += (self._bracket_open or "[") + self._bracket_buf
        if self._deferring:
            # 收尾仍在等后置说话人（后面没等到说话动词）：原样归还旁白
            self.pending += narr_quote_span(
                self._deferred_open, self._deferred_buf, self._deferred_close,
            ) + self._deferred_tail
        yield from self._flush_pending()

    def write_result(self, full_response: str) -> None:
        """把最终产物写回调用方传进来的 result 列表（[旁白, 全文, 气泡, 对话位点, 分组]）。"""
        self.result[0] = self.narration
        self.result[1] = full_response
        if len(self.result) > 2:
            self.result[2] = self.extracted
        if len(self.result) > 3:
            self.result[3] = self.dialogue_marks
        if len(self.result) > 4:
            self.result[4] = self.group_marks
