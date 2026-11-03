"""规则书 RAG：PDF 入库（按词条解析 + 子块嵌入）与按需检索。

设计取舍（见技术选型）：规模极小（三本书约 4k 子块），不引入独立向量库，向量以 float32
BLOB 存进现有 SQLite，查询时 numpy 暴力余弦，零新基础设施。嵌入走可插拔 ``Embedder``
（模型可在设置页切换）；每本规则书记录建索引的模型（``Rulebook.embed_model``），检索时
按它选嵌入器查询。

索引结构（小块检索、整条返回）：
- PDF 按字号解析成**词条**（``RuleSection``：标题、路径、起止页、全文，见 ``rulebook_parser``）；
- 词条正文按句切成约 200 字的**子块**（``RuleChunk``），入向量时前面拼上词条路径；
- 检索命中子块后，返回它所属的**整条词条**（长词条截取命中附近），KP 拿到的是完整条文，
  不再是被切在句子中间的半截话。
旧版导入的规则书（没有词条结构，``section_id`` 为空）仍按块返回；重新上传即升级。
本机保存 PDF 原件：切块逻辑升级（``CHUNKER_VERSION``）后，有原件的书在后台自动重建。
"""

from __future__ import annotations

import logging
import re
import uuid
from collections import Counter
from collections.abc import Callable
from pathlib import Path

import numpy as np
from sqlalchemy.orm import Session

from app.ai.embedding import Embedder, embedder_for, get_embedder
from app.config import settings
from app.models.rulebook import RuleChunk, Rulebook, RuleSection
from app.services import rulebook_parser
from app.services.vector_search import grouped_top_k

logger = logging.getLogger(__name__)

#: 切块逻辑版本：改了解析或切块就加一，有原件的规则书会在启动时后台重建
CHUNKER_VERSION = 2
#: 本机保存的规则书 PDF 原件目录
RULEBOOKS_DIR = settings.db_path.parent / "rulebooks"
#: 返回给 KP 的单条词条最多这么多字；更长的词条截取命中子块附近
SECTION_MAX_CHARS = 1000
#: 截取窗口里命中子块之前保留的字数（让 KP 看到命中句的上文）
_WINDOW_LEAD_CHARS = 250
#: 嵌入时每批多少子块汇报一次进度（也是检查「是否被取消」的粒度）
_EMBED_PROGRESS_BATCH = 64

# 测试注入点：解析 PDF（返回 ``(页数, 词条列表)``）
parse_pdf = rulebook_parser.parse_pdf


# ---------------------------------------------------------------------------
# 原件
# ---------------------------------------------------------------------------

class IngestCancelled(Exception):
    """导入途中规则书被删除。"""


def source_path(rulebook: Rulebook) -> Path | None:
    return RULEBOOKS_DIR / rulebook.source_file if rulebook.source_file else None


def save_source(rulebook: Rulebook, pdf_bytes: bytes) -> None:
    """把 PDF 原件存进数据目录（以后切块逻辑升级时据此重建，不必让房主重新上传）。"""
    RULEBOOKS_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{rulebook.id}.pdf"
    (RULEBOOKS_DIR / name).write_bytes(pdf_bytes)
    rulebook.source_file = name


# ---------------------------------------------------------------------------
# 入库
# ---------------------------------------------------------------------------

def ingest_rulebook(
    db: Session,
    rulebook: Rulebook,
    pdf_bytes: bytes,
    embedder: Embedder | None = None,
    *,
    keep_on_failure: bool = False,
    parsed: tuple[int, list[rulebook_parser.ParsedSection]] | None = None,
    on_progress: Callable[[str, int, int], bool] | None = None,
) -> Rulebook:
    """对 Rulebook 记录做入库：解析词条 → 切子块 → 嵌入 → 整体替换旧索引。

    成功置 status=ready。失败时：首次入库置 failed 并记 error；``keep_on_failure``
    （升级重建）则保留旧索引照常可用，只记 error。新旧索引在同一事务里替换，读者不会看到半截。
    ``parsed`` 为已解析好的结果（导入队列在子进程里解析）；``on_progress(阶段, 已完成, 总数)``
    返回 False 表示规则书已被删除，就此中止、不再写库。
    """
    def report(stage: str, done: int, total: int) -> None:
        if on_progress is not None and not on_progress(stage, done, total):
            raise IngestCancelled

    try:
        embedder = embedder or get_embedder()
        page_count, sections = parsed if parsed is not None else parse_pdf(pdf_bytes)
        section_rows: list[RuleSection] = []
        chunk_rows: list[RuleChunk] = []
        embed_inputs: list[str] = []
        for si, sec in enumerate(sections):
            path = " › ".join(sec.path)
            row = RuleSection(
                id=str(uuid.uuid4()), rulebook_id=rulebook.id, ordinal=si, level=sec.level,
                title=sec.title, path=path, page_start=sec.page_start, page_end=sec.page_end,
                text=sec.text,
            )
            section_rows.append(row)
            for at, piece in rulebook_parser.split_section(sec.text):
                chunk_rows.append(RuleChunk(
                    rulebook_id=rulebook.id, rule_system=rulebook.rule_system,
                    page=sec.page_at(at), ordinal=len(chunk_rows), text=piece, section_id=row.id,
                ))
                embed_inputs.append(rulebook_parser.embed_text(path, piece))
        if not chunk_rows:
            raise ValueError("未从 PDF 抽取到可用文本（可能是扫描件，需 OCR）")

        vectors: list[list[float]] = []
        total = len(embed_inputs)
        report("embedding", 0, total)
        for at in range(0, total, _EMBED_PROGRESS_BATCH):
            vectors.extend(embedder.embed_passages(embed_inputs[at:at + _EMBED_PROGRESS_BATCH]))
            report("embedding", len(vectors), total)
        report("saving", total, total)
        if len(vectors) != len(chunk_rows):
            raise ValueError("嵌入数量与切块数量不一致")
        for row, vec in zip(chunk_rows, vectors):
            row.embedding = np.asarray(vec, dtype=np.float32).tobytes()

        db.query(RuleChunk).filter(RuleChunk.rulebook_id == rulebook.id).delete()
        db.query(RuleSection).filter(RuleSection.rulebook_id == rulebook.id).delete()
        db.add_all(section_rows)
        db.add_all(chunk_rows)
        rulebook.page_count = page_count
        rulebook.chunk_count = len(chunk_rows)
        rulebook.embed_model = embedder.model_name
        rulebook.chunker_version = CHUNKER_VERSION
        rulebook.status = "ready"
        rulebook.error = ""
        db.commit()
        db.refresh(rulebook)
        logger.info(
            "规则书入库完成：%s（%d 页 / %d 词条 / %d 子块）",
            rulebook.title, page_count, len(section_rows), len(chunk_rows),
        )
    except IngestCancelled:
        db.rollback()  # 规则书已被删除：什么都不写
        logger.info("规则书导入已取消：%s", rulebook.id)
    except Exception as exc:  # noqa: BLE001 — 入库失败要落状态而非吞掉
        db.rollback()
        if not keep_on_failure:
            rulebook.status = "failed"
        rulebook.error = str(exc)[:500]
        db.commit()
        logger.exception("规则书入库失败：%s", rulebook.title)
    return rulebook


def chunk_embed_texts(db: Session, chunks: list[RuleChunk]) -> list[str]:
    """这些子块入向量时用的文本（换嵌入模型重建时要与入库时完全一致）。"""
    ids = {c.section_id for c in chunks if c.section_id}
    paths = dict(db.query(RuleSection.id, RuleSection.path).filter(RuleSection.id.in_(ids)).all()) if ids else {}
    return [
        rulebook_parser.embed_text(paths.get(c.section_id, ""), c.text) if c.section_id else c.text
        for c in chunks
    ]


def outdated_rulebooks(db: Session) -> list[Rulebook]:
    """切块逻辑已升级、且本机存着原件的就绪规则书（可在后台自动重建）。"""
    return [
        b for b in db.query(Rulebook).filter(
            Rulebook.status == "ready", Rulebook.chunker_version < CHUNKER_VERSION,
            Rulebook.source_file != "",
        )
        if (p := source_path(b)) is not None and p.exists()
    ]


# ---------------------------------------------------------------------------
# 检索
# ---------------------------------------------------------------------------

def retrieve(
    db: Session,
    query: str,
    rule_system: str,
    k: int = 3,
    embedder: Embedder | None = None,
) -> list[dict]:
    """检索与 query 最相关的规则书词条。

    返回 ``[{"text", "page", "score", "rulebook_id", "title_match", "section", "truncated"}, ...]``，
    按相关度降序：子块语义排序归并到词条后，与小节标题命中融合（见 ``_fuse``）；``score``
    是命中子块的语义余弦分。``text`` 以「【词条路径】」开头。无已就绪的规则书时返回空列表。
    """
    query = (query or "").strip()
    if not query:
        return []
    rows = db.query(RuleChunk).filter(RuleChunk.rule_system == rule_system).all()
    if not rows:
        return []

    # 按建索引的模型分组查询（显式传 embedder 时一律用它，供测试注入）。取全序：标题命中的
    # 词条可能语义排名很靠后，融合时要知道它的语义名次。
    models = dict(db.query(Rulebook.id, Rulebook.embed_model).all())
    dense = grouped_top_k(
        [r.embedding for r in rows], [models.get(r.rulebook_id) for r in rows], query, len(rows),
        resolve=(lambda _m: embedder) if embedder is not None else embedder_for,
    )
    if not dense:
        return []

    # 归并到检索单元：新版按词条，旧版（无词条结构）按块
    units: list[str] = []
    best: dict[str, tuple[int, float]] = {}  # 单元 → (命中子块下标, 语义分)
    for i, score in dense:
        unit = rows[i].section_id or f"chunk:{rows[i].id}"
        if unit not in best:
            best[unit] = (i, score)
            units.append(unit)
    section_ids = [u for u in units if not u.startswith("chunk:")]
    sections = {
        s.id: s for s in db.query(RuleSection).filter(RuleSection.id.in_(section_ids)).all()
    } if section_ids else {}
    legacy = _legacy_titles([rows[best[u][0]].text if u not in sections else "" for u in units])
    titles = [_clean_title(sections[u].title) if u in sections else legacy[n] for n, u in enumerate(units)]

    hits: list[dict] = []
    for n, score, rank, matched in _fuse(query, titles, [(n, best[u][1]) for n, u in enumerate(units)])[: k + 2]:
        i = best[units[n]][0]
        chunk = rows[i]
        sec = sections.get(units[n])
        if sec is not None:
            # 标题命中的词条从头截取（标题已说明主题，开头就是定义）；语义命中的截取命中子块附近
            text, truncated = _section_context(sec, None if matched else chunk.text)
            hits.append({
                "text": text, "page": chunk.page, "score": score, "rulebook_id": chunk.rulebook_id,
                "title_match": matched, "section": sec.path, "truncated": truncated,
                "rank": rank, "ordinal": None,
            })
        else:
            hits.append({
                "text": chunk.text, "page": chunk.page, "score": score, "rulebook_id": chunk.rulebook_id,
                "title_match": matched, "section": "", "truncated": False,
                "rank": rank, "ordinal": chunk.ordinal,
            })
    legacy = _merge_adjacent_hits([h for h in hits if h["ordinal"] is not None])
    merged = [h for h in hits if h["ordinal"] is None] + legacy
    merged.sort(key=lambda h: h["rank"], reverse=True)
    for h in merged:
        h.pop("ordinal", None)
        h.pop("rank", None)
    return merged[:k]


def _section_context(sec: RuleSection, hit_text: str | None) -> tuple[str, bool]:
    """整条词条（带路径头）；超长时截取命中子块附近一段（``hit_text`` 为空则从头截取），并注明已截取。"""
    head = f"【{sec.path}】\n" if sec.path else ""
    body = sec.text
    if len(body) <= SECTION_MAX_CHARS:
        return head + body, False
    found = 0
    if hit_text:
        found = body.find(hit_text)
        if found < 0:
            found = max(0, body.find(hit_text[:40]))
    at = max(0, found - _WINDOW_LEAD_CHARS)
    at = min(at, len(body) - SECTION_MAX_CHARS)
    window = body[at:at + SECTION_MAX_CHARS]
    prefix = "……" if at > 0 else ""
    suffix = "……" if at + SECTION_MAX_CHARS < len(body) else ""
    return f"{head}{prefix}{window}{suffix}（本节较长，已截取命中部分）", True


def _find_overlap(a: str, b: str, min_overlap: int = 15, max_overlap: int = 120) -> int:
    """a 的后缀与 b 的前缀的最长重叠长度（滑窗块之间的 80 字重叠，各自 strip 可能略缩短）；
    不足 ``min_overlap`` 视为无重叠（不同小节的块不该被拼）。"""
    limit = min(len(a), len(b), max_overlap)
    for n in range(limit, min_overlap - 1, -1):
        if a[-n:] == b[:n]:
            return n
    return 0


def _merge_adjacent_hits(hits: list[dict]) -> list[dict]:
    """旧版切块：把**同页且 ordinal 相邻、文本确有滑窗重叠**的命中块合并成一段。

    否则同一长节的两个孪生块（含 80 字重叠）会占掉两个 top-k 名额、注入内容近半重复。
    三个条件缺一不可：ordinal 相邻但分属不同小节/不同页的块（内容不连续）绝不能拼在一起
    冒充连续原文。新版按词条返回，用不到这一步。
    """
    if len(hits) <= 1:
        return hits
    hits = sorted(hits, key=lambda h: (h["rulebook_id"], h["ordinal"]))
    out = [dict(hits[0])]
    for h in hits[1:]:
        prev = out[-1]
        overlap = 0
        if (
            h["rulebook_id"] == prev["rulebook_id"]
            and h["page"] == prev["page"]
            and h["ordinal"] == prev["ordinal"] + 1
        ):
            overlap = _find_overlap(prev["text"], h["text"])
        if overlap:
            prev["text"] = prev["text"] + h["text"][overlap:]
            prev["score"] = max(prev["score"], h["score"])
            prev["rank"] = max(prev["rank"], h["rank"])
            prev["title_match"] = prev["title_match"] or h["title_match"]
            prev["ordinal"] = h["ordinal"]   # 三连及以上的相邻块继续并入
        else:
            out.append(dict(h))
    return out


# ---------------------------------------------------------------------------
# 小节标题命中：术语查询（「孤注一掷」「抵近射击」）优先返回以它为标题的词条
# ---------------------------------------------------------------------------

#: 倒数排名融合（RRF）的平滑常数，取业界常用值
_RRF_K = 60
#: 旧版切块里同一「标题」出现这么多次以上，视为混进来的页眉（【第四章 技能】每页都有）。
#: 新版词条的页眉在解析时已去掉，「孤注一掷」这类多处共用的真标题不能被这条误伤。
_PAGE_HEADER_MIN_REPEATS = 4
_TITLE_RE = re.compile(r"^【([^】]{1,30})】")
_TITLE_NUMBERING_RE = re.compile(r"^(?:[\d.\s⊙●○◎·•※★☆■□◆◇▲△]+|第[一二三四五六七八九十百]+[章节]\s*)")
_PAREN_RE = re.compile(r"[（(][^）)]*[）)]")
_QUERY_STRIP_RE = re.compile(r"[\s，。、；：？！?!,.;:“”\"'「」《》（）()]")
#: 术语查询常带的问句外壳：剥掉后剩下的词若正好是某个小节标题，按完全命中处理
#: （「孤注一掷是什么」→「孤注一掷」）。只剥这几种固定说法，不做「标题出现在查询里」的
#: 泛匹配——那会让「生命值降到0会发生什么」之类口语提问被某个短标题劫持。
_QUESTION_AFFIX_RE = re.compile(
    r"^(?:什么是|何为|怎么进行|如何进行|怎样进行|怎么|如何|怎样)"
    r"|(?:是什么意思|是什么|是啥|怎么用|怎么算|怎么判定|如何使用|有什么用|的规则|规则|的定义|定义)$"
)
#: 与小节标题完全相同时加的分：大于任何融合分之和，保证这一条排最前
_EXACT_TITLE_BONUS = 1.0


def _clean_title(title: str) -> str:
    """去掉「5.3」「第四章」「⊙」之类的编号与符号前缀。"""
    return _TITLE_NUMBERING_RE.sub("", title).strip()


def _section_title(text: str) -> str:
    """旧版切块写在块开头的【小节标题】。"""
    m = _TITLE_RE.match(text)
    return _clean_title(m.group(1)) if m else ""


def _legacy_titles(texts: list[str]) -> list[str]:
    """旧版切块的标题（块开头的【…】），重复出现的页眉置空；空串表示不是旧版块。"""
    titles = [_section_title(t) if t else "" for t in texts]
    repeats = Counter(t for t in titles if t)
    return [t if t and repeats[t] < _PAGE_HEADER_MIN_REPEATS else "" for t in titles]


def _fuse(
    query: str, titles: list[str], dense: list[tuple[int, float]],
) -> list[tuple[int, float, float, bool]]:
    """把「标题命中」与语义排序做倒数排名融合，返回 ``[(单元下标, 语义分, 融合分, 是否标题命中)]``。

    ``titles[n]`` 是第 n 个检索单元的标题，``dense`` 是单元的语义排序（从好到差）。

    只按语义排序时，高频术语会被大量「提到它的段落」淹没（「孤注一掷」在守秘人规则书里
    出现于上百处，每个技能条目都有「孤注一掷的例子」），真正的定义反而排不上来。标题是
    排版给出的结构信号：查询与标题完全相同的必排最前；标题主体包含查询次之（越短越像
    定义）；只在标题括号附注里出现的再次之。没有任何标题命中的查询（口语化提问基本如此）
    排序与纯语义完全相同。
    """
    q = _QUERY_STRIP_RE.sub("", query)
    core = _QUESTION_AFFIX_RE.sub("", q)
    scored = {n for n, _s in dense}
    matches: list[tuple[int, float]] = []
    if len(q) >= 2:
        for n, t in enumerate(titles):
            if not t or n not in scored:
                continue
            # 括号里的多是附注（「瞄准（奖励骰）」讲的是瞄准），只在括号里命中算弱命中
            main = _PAREN_RE.sub("", t).strip()
            if q in (t, main) or (len(core) >= 2 and core in (t, main)):
                matches.append((n, 3.0))
            elif main and q in main:
                matches.append((n, 2.0 + len(q) / len(main)))
            elif q in t:
                matches.append((n, 1.0 + len(q) / len(t)))
    dense_rank = {n: r for r, (n, _s) in enumerate(dense)}
    matches.sort(key=lambda m: (-m[1], dense_rank[m[0]]))
    title_rank = {n: r for r, (n, _w) in enumerate(matches)}
    exact = {n for n, w in matches if w == 3.0}

    fused = []
    for n, score in dense:
        rank = 1 / (_RRF_K + dense_rank[n] + 1)
        if n in title_rank:
            rank += 1 / (_RRF_K + title_rank[n] + 1)
        if n in exact:
            rank += _EXACT_TITLE_BONUS
        fused.append((n, score, rank, n in title_rank))
    # 融合分打平时（一条语义靠前、一条标题靠前）让标题命中更强的优先
    fused.sort(key=lambda f: (-f[2], title_rank.get(f[0], len(dense))))
    return fused


# ---------------------------------------------------------------------------
# 其他
# ---------------------------------------------------------------------------

def has_rulebook(db: Session, rule_system: str) -> bool:
    """该规则系统是否已有可检索（ready）的规则书。"""
    return (
        db.query(Rulebook.id)
        .filter(Rulebook.rule_system == rule_system, Rulebook.status == "ready")
        .first()
        is not None
    )


def list_rulebooks(db: Session) -> list[Rulebook]:
    return db.query(Rulebook).order_by(Rulebook.created_at.desc()).all()


def delete_rulebook(db: Session, rulebook_id: str) -> bool:
    book = db.get(Rulebook, rulebook_id)
    if not book:
        return False
    source = source_path(book)
    # 显式删子块与词条（SQLite 默认不强制级联，且测试库未必开外键）
    db.query(RuleChunk).filter(RuleChunk.rulebook_id == rulebook_id).delete()
    db.query(RuleSection).filter(RuleSection.rulebook_id == rulebook_id).delete()
    db.delete(book)
    db.commit()
    if source is not None:
        source.unlink(missing_ok=True)
    return True
