"""规则书 PDF 结构解析：按字号识别标题层级，组装成词条（小节）与检索用的子块。

为什么按字号而不是按行长度猜标题：规则书 PDF 的正文行常常只有十来个字（窄栏排版），
「不超过 14 字且无句读」的旧规则会把正文半句（如「或汽车驾驶技能发起」）误当标题，
在那里切开，碎片短于阈值又被丢弃——守秘人规则书约 40% 的块因此从句子中间开始，
「追逐轮中的行动」的整段列表直接没进库。而排版里标题的字号总是大于正文（正文 10.6、
词条标题 12、节标题 16、章标题 30），这个信号可靠得多。

两层：
- ``read_pdf_lines``：pypdf 逐页取出「行文字 + 该行字号」。
- ``build_sections``：纯函数，从行与字号组装词条（单测直接喂合成数据）。
"""
from __future__ import annotations

import io
import logging
import re
from collections import Counter
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

#: 子块目标字数：本机评测（三本规则书、术语 / 完整性 / 口语查询）里 200 与 350 相当、500 明显变差
CHILD_CHUNK_CHARS = 200
#: 标题字号至少比正文大这么多倍
HEADING_SIZE_RATIO = 1.1
HEADING_MAX_CHARS = 30
#: 同样文字、同样字号在这么多页以上出现：页眉页脚
PAGE_HEADER_MIN_PAGES = 5

#: (文字, 字号) 或 (文字, 字号, 是否粗体)
Line = tuple
#: 一行里粗体字占比达到这个比例才算粗体行（句中强调的几个字不算）
BOLD_LINE_RATIO = 0.8

#: 行首的项目符号（含 PDF 常用的 Wingdings 私用区字形，如 U+F06C）
_BULLET_RE = re.compile(r"^[•▪■◆●\uf000-\uf0ff]\s*")
_NOT_TITLE_RE = re.compile(r"[。，；]")
_NUMERIC_RE = re.compile(r"[\d\s.·\-—]+")
_SENTENCE_RE = re.compile(r"(?<=[。！？；!?;])|(?=\n· )")


@dataclass
class ParsedSection:
    level: int  # 0 = 最高层（章）；-1 = 第一个标题之前的前言
    title: str
    path: list[str]
    page_start: int
    page_end: int
    text: str
    #: [(正文内字符偏移, 页码)]：子块据此标注自己所在的页
    page_marks: list[tuple[int, int]] = field(default_factory=list)

    def page_at(self, offset: int) -> int:
        page = self.page_start
        for at, p in self.page_marks:
            if at > offset:
                break
            page = p
        return page


# ---------------------------------------------------------------------------
# PDF → 行与字号
# ---------------------------------------------------------------------------

def read_pdf_lines(pdf_bytes: bytes) -> list[list[Line]]:
    """逐页返回 ``[(行文字, 字号)]``；字号取该行按字数加权最多的那个（行内混排时以主体为准）。"""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf_bytes))
    pages: list[list[Line]] = []
    for i, page in enumerate(reader.pages, start=1):
        try:
            pages.append(_page_lines(page))
        except Exception:  # noqa: BLE001 — 个别坏页不应中断整本入库
            logger.warning("规则书第 %d 页文本抽取失败，跳过", i)
            pages.append([])
    return pages


def _page_lines(page) -> list[Line]:
    """逐行取 ``(文字, 字号, 是否粗体)``。

    粗体看文字渲染模式：不少中文排版用「填充+描边」（Tr 2）做伪粗体标出小标题，字号与字体都和
    正文一样（守秘人规则书的「抵近射击（奖励骰）」即是）。渲染模式属于图形状态，要随 q/Q 入栈出栈。
    """
    lines: list[Line] = []
    cur: list[str] = []
    sizes: Counter[float] = Counter()
    bold_chars = 0
    render_mode = [0.0]  # 图形状态栈上的文字渲染模式

    def flush() -> None:
        nonlocal bold_chars
        text = "".join(cur).strip()
        if text:
            total = sum(sizes.values()) or 1
            lines.append((
                text, sizes.most_common(1)[0][0] if sizes else 0.0,
                bold_chars / total >= BOLD_LINE_RATIO,
            ))
        cur.clear()
        sizes.clear()
        bold_chars = 0

    def before(op, args, _cm, _tm) -> None:
        if op == b"q":
            render_mode.append(render_mode[-1])
        elif op == b"Q" and len(render_mode) > 1:
            render_mode.pop()
        elif op == b"Tr" and args:
            render_mode[-1] = float(args[0])

    def visit(text, cm, tm, _font, size) -> None:
        nonlocal bold_chars
        effective = round(float(size) * (tm[3] or 1) * (cm[3] or 1), 1)
        bold = render_mode[-1] == 2.0
        for n, part in enumerate(text.split("\n")):
            if n:
                flush()
            if part:
                cur.append(part)
                if part.strip():
                    sizes[effective] += len(part.strip())
                    if bold:
                        bold_chars += len(part.strip())

    page.extract_text(visitor_operand_before=before, visitor_text=visit)
    flush()
    return lines


# ---------------------------------------------------------------------------
# 行 → 词条
# ---------------------------------------------------------------------------

def build_sections(pages: list[list[Line]]) -> list[ParsedSection]:
    """按字号把逐页的行组装成词条；标题层级由字号从大到小排出，跨页的词条自然连续。

    比正文大的字号各成一级；正文字号的整行粗体是最深一级（小标题）。
    """
    pages = [[(ln[0], ln[1], ln[2] if len(ln) > 2 else False) for ln in lines] for lines in pages]
    weight: Counter[float] = Counter()
    for lines in pages:
        for text, size, _bold in lines:
            weight[size] += len(text)
    if not weight:
        return []
    body = weight.most_common(1)[0][0]
    repeats = Counter((t, s) for lines in pages for t, s in {(t, s) for t, s, _b in lines})
    page_furniture = {
        line for line, n in repeats.items()
        if n >= PAGE_HEADER_MIN_PAGES and len(line[0]) <= HEADING_MAX_CHARS
    }

    def is_heading(text: str, size: float, bold: bool = False) -> bool:
        return (
            (size >= body * HEADING_SIZE_RATIO or (bold and size >= body))
            and 2 <= len(text) <= HEADING_MAX_CHARS  # 单字是段首放大的首字下沉，不是标题
            and not _NUMERIC_RE.fullmatch(text)
            and not _NOT_TITLE_RE.search(text)
            and (text, size) not in page_furniture
        )

    heading_sizes = sorted(
        {s for lines in pages for t, s, b in lines if is_heading(t, s, b) and s >= body * HEADING_SIZE_RATIO},
        reverse=True,
    )
    has_bold_headings = any(is_heading(t, s, b) and s < body * HEADING_SIZE_RATIO for lines in pages for t, s, b in lines)
    if not heading_sizes and not has_bold_headings:
        return _sections_by_page(pages, body, page_furniture)
    level_of = {s: n for n, s in enumerate(heading_sizes)}
    bold_level = len(heading_sizes)  # 正文字号的粗体小标题：最深一级

    sections: list[ParsedSection] = []
    stack: list[tuple[int, str]] = []
    parts: list[str] = []
    cur: ParsedSection | None = None
    title_open = False  # 刚开了一个标题、还没有正文：同字号的下一行是标题折行

    def close() -> None:
        if cur is not None and parts:
            cur.text = "".join(parts).strip()  # 开头的项目符号换行一并去掉
            if cur.text:
                sections.append(cur)

    for page_no, lines in enumerate(pages, start=1):
        for text, size, bold in lines:
            if (text, size) in page_furniture or (size != body and re.fullmatch(r"\d{1,4}", text)):
                continue  # 页眉页脚、页码
            if is_heading(text, size, bold):
                level = level_of.get(size, bold_level)
                if title_open and stack and stack[-1][0] == level and cur is not None:
                    merged = stack[-1][1] + text
                    stack[-1] = (level, merged)
                    cur.title, cur.path = merged, [t for _, t in stack]
                    continue
                close()
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, text))
                cur = ParsedSection(level, text, [t for _, t in stack], page_no, page_no, "")
                parts = []
                title_open = True
                continue
            title_open = False
            if cur is None:
                cur = ParsedSection(-1, "", [], page_no, page_no, "")
                parts = []
            line = _BULLET_RE.sub("\n· ", text) if _BULLET_RE.match(text) else text
            if parts and _needs_space(parts[-1], line):
                line = " " + line
            offset = sum(len(p) for p in parts)
            if not cur.page_marks or cur.page_marks[-1][1] != page_no:
                cur.page_marks.append((offset, page_no))
            parts.append(line)
            cur.page_end = page_no
    close()
    return sections


def _sections_by_page(pages, body, page_furniture) -> list[ParsedSection]:
    """识别不出任何标题（扫描后 OCR 的书、字号全一样）：退回一页一个词条，至少不丢内容。"""
    out = []
    for page_no, lines in enumerate(pages, start=1):
        parts: list[str] = []
        for text, size, _bold in lines:
            if (text, size) in page_furniture or (size != body and re.fullmatch(r"\d{1,4}", text)):
                continue
            parts.append((" " if parts and _needs_space(parts[-1], text) else "") + text)
        text = "".join(parts).strip()
        if text:
            out.append(ParsedSection(0, "", [], page_no, page_no, text, [(0, page_no)]))
    return out


def _needs_space(prev: str, nxt: str) -> bool:
    """中文硬换行直接相连；英文单词之间补一个空格。"""
    a, b = prev[-1:], nxt[:1]
    return a.isascii() and a.isalnum() and b.isascii() and b.isalnum()


# ---------------------------------------------------------------------------
# 词条 → 子块
# ---------------------------------------------------------------------------

def split_section(text: str, size: int = CHILD_CHUNK_CHARS) -> list[tuple[int, str]]:
    """按句子边界把词条正文切成约 ``size`` 字的子块，返回 ``[(起始偏移, 子块)]``。

    相邻子块带一句重叠（短于 size/4 时），避免答案正好落在边界上两边都不完整；超长的单句硬切。
    """
    sentences = [s for s in _SENTENCE_RE.split(text) if s]
    out: list[tuple[int, str]] = []
    cur, cur_at, pos = "", 0, 0
    for s in sentences:
        while len(s) > size:
            if cur.strip():
                out.append((cur_at, cur))
            out.append((pos, s[:size]))
            pos += size
            s = s[size:]
            cur, cur_at = "", pos
        if cur and len(cur) + len(s) > size:
            out.append((cur_at, cur))
            tail = _SENTENCE_RE.split(cur)[-1] if cur else ""
            if 0 < len(tail) < size // 4:
                cur, cur_at = tail, pos - len(tail)
            else:
                cur, cur_at = "", pos
        if not cur:
            cur_at = pos
        cur += s
        pos += len(s)
    if cur.strip():
        out.append((cur_at, cur))
    return [(at, piece.strip()) for at, piece in out if piece.strip()]


def embed_text(path: str, chunk_text: str) -> str:
    """子块入向量的文本：前面拼上词条路径的最后三级，长词条后半段的子块也不会丢掉主语。"""
    tail = " › ".join(path.split(" › ")[-3:]) if path else ""
    return f"{tail}\n{chunk_text}" if tail else chunk_text


def parse_pdf(pdf_bytes: bytes) -> tuple[int, list[ParsedSection]]:
    """解析规则书 PDF，返回 ``(页数, 词条列表)``。"""
    pages = read_pdf_lines(pdf_bytes)
    return len(pages), build_sections(pages)
