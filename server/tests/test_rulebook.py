"""规则书 RAG 入库与检索的单元测试。

不下载真实嵌入模型、不读真实 PDF：用确定性 FakeEmbedder（按字符散列成词袋向量，余弦
相似度即反映字符重叠），并用 parse_pdf 替身直接给出词条，验证「词条→子块→嵌入→存储→
检索→整条返回」链路与降级。PDF 解析本身见 test_rulebook_parser。
"""

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.ai.embedding import Embedder
from app.models import Base, RuleChunk, Rulebook, RuleSection  # noqa: F401 注册表
from app.services import rulebook_service
from app.services.rulebook_parser import ParsedSection


class FakeEmbedder(Embedder):
    model_name = "fake-test"
    dim = 64

    def _vec(self, t: str):
        v = np.zeros(self.dim, dtype=np.float32)
        for ch in t:
            v[ord(ch) % self.dim] += 1.0
        return v.tolist()

    def embed_passages(self, texts):
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)


@pytest.fixture
def db_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _book(db, title="测试规则书", rule_system="coc"):
    book = Rulebook(title=title, rule_system=rule_system, status="indexing")
    db.add(book)
    db.commit()
    return book


def _canned(*items):
    """parse_pdf 的替身：items 为 (标题, 正文, 页码)，都挂在「第五章 游戏系统」下。"""
    secs = [
        ParsedSection(1, t, ["第五章 游戏系统", t], p, p, text, [(0, p)]) for t, text, p in items
    ]
    return lambda _b: (max(p for _t, _x, p in items), secs)


class SpyEmbedder(FakeEmbedder):
    def __init__(self):
        self.passages = []

    def embed_passages(self, texts):
        self.passages.extend(texts)
        return super().embed_passages(texts)


def test_ingest_and_retrieve_roundtrip(db_factory, monkeypatch):
    db = db_factory()
    monkeypatch.setattr(rulebook_service, "parse_pdf", _canned(
        ("技能检定", "投掷 d100，结果小于等于技能值即为普通成功，小于等于一半为困难成功，难度由守秘人根据情境选定。", 1),
        ("孤注一掷", "当一次技能检定失败后，玩家可以加倍投入资源重新掷骰一次；若再次失败，后果将显著加重，由守秘人裁定。", 2),
        ("理智检定", "当调查员遭遇超自然或恐怖事物时进行理智检定，失败会损失理智值，理智值归零将陷入永久性的疯狂状态。", 3),
    ))
    book = _book(db)
    rulebook_service.ingest_rulebook(db, book, b"%PDF-fake", embedder=FakeEmbedder())

    assert book.status == "ready"
    assert book.chunk_count == 3
    assert book.embed_model == "fake-test"
    assert book.chunker_version == rulebook_service.CHUNKER_VERSION
    assert db.query(RuleSection).count() == 3
    assert all(c.section_id for c in db.query(RuleChunk))

    hits = rulebook_service.retrieve(db, "孤注一掷 重掷", "coc", k=2, embedder=FakeEmbedder())
    assert hits[0]["section"] == "第五章 游戏系统 › 孤注一掷"
    assert hits[0]["text"].startswith("【第五章 游戏系统 › 孤注一掷】\n当一次技能检定失败后")
    assert hits[0]["page"] == 2
    assert hits[0]["title_match"] is False          # 查询不等于标题
    assert rulebook_service.retrieve(db, "孤注一掷", "coc", k=1, embedder=FakeEmbedder())[0]["title_match"]
    assert "ordinal" not in hits[0] and "rank" not in hits[0]   # 内部字段不外泄


def test_命中子块时返回整条词条(db_factory, monkeypatch):
    """「追逐轮中的行动」那种列表：命中后半段的子块，返回的仍是从头到尾完整的词条。"""
    body = (
        "在其依敏捷次序决定的回合中，每一个角色都可以使用其回合来：在追逐中前进。"
        + "说明文字。" * 40
        + "主动使用格斗、射击或汽车驾驶技能发起一次攻击。施放一个法术。进行其他动作，例如撬开一把锁。"
    )
    monkeypatch.setattr(rulebook_service, "parse_pdf", _canned(("追逐轮中的行动", body, 114)))
    db = db_factory()
    rulebook_service.ingest_rulebook(db, _book(db), b"x", embedder=FakeEmbedder())
    assert db.query(RuleChunk).count() > 1          # 切成了多个子块

    hits = rulebook_service.retrieve(db, "撬开一把锁 施放一个法术", "coc", k=3, embedder=FakeEmbedder())
    assert len(hits) == 1                            # 同一词条的多个子块只占一个名额
    assert hits[0]["text"].endswith(body)            # 完整词条
    assert hits[0]["truncated"] is False


def test_超长词条截取命中附近并注明(db_factory, monkeypatch):
    body = "甲" * 1500 + "孤注一掷的关键条文在这里。" + "乙" * 1500
    monkeypatch.setattr(rulebook_service, "parse_pdf", _canned(("长词条", body, 5)))
    db = db_factory()
    rulebook_service.ingest_rulebook(db, _book(db), b"x", embedder=FakeEmbedder())

    hit = rulebook_service.retrieve(db, "孤注一掷的关键条文", "coc", k=1, embedder=FakeEmbedder())[0]
    assert hit["truncated"] is True
    assert "孤注一掷的关键条文在这里" in hit["text"]
    assert hit["text"].endswith("（本节较长，已截取命中部分）")
    assert len(hit["text"]) < len(body)


def test_标题命中的长词条从开头截取(db_factory, monkeypatch):
    """「5.3 孤注一掷」开头是一段引文：语义最近的子块可能落在引文里，但标题已说明主题，应从开头给。"""
    body = "引文。" * 100 + "孤注一掷为玩家提供了第二次也是最后一次机会。" + "说明。" * 400
    monkeypatch.setattr(rulebook_service, "parse_pdf", _canned(("孤注一掷", body, 74)))
    db = db_factory()
    rulebook_service.ingest_rulebook(db, _book(db), b"x", embedder=FakeEmbedder())
    hit = rulebook_service.retrieve(db, "孤注一掷", "coc", k=1, embedder=FakeEmbedder())[0]
    assert hit["title_match"] and hit["truncated"]
    assert hit["text"].startswith("【第五章 游戏系统 › 孤注一掷】\n引文。")
    assert "第二次也是最后一次机会" in hit["text"]


def test_子块入向量时带词条路径_换模型重建用同样的文本(db_factory, monkeypatch):
    monkeypatch.setattr(rulebook_service, "parse_pdf", _canned(("行动点", "所有角色每一轮都能获得一定数量的行动点。", 9)))
    db = db_factory()
    spy = SpyEmbedder()
    rulebook_service.ingest_rulebook(db, _book(db), b"x", embedder=spy)
    assert spy.passages == ["第五章 游戏系统 › 行动点\n所有角色每一轮都能获得一定数量的行动点。"]
    rows = db.query(RuleChunk).all()
    assert rulebook_service.chunk_embed_texts(db, rows) == spy.passages


def _legacy_chunk(db, book, ordinal, text, page=1, emb=None):
    emb = emb or FakeEmbedder()
    db.add(RuleChunk(
        rulebook_id=book.id, rule_system=book.rule_system, page=page, ordinal=ordinal, text=text,
        embedding=np.asarray(emb._vec(text), dtype=np.float32).tobytes(),
    ))


def test_旧版索引照常检索并合并相邻重叠块(db_factory):
    """没有词条结构的存量索引：命中块本身返回，同一长节的孪生滑窗块合并成一段。"""
    db = db_factory()
    book = _book(db)
    book.status, book.embed_model = "ready", "fake-test"
    page_text = "孤注一掷规则" + "".join(chr(0x4E00 + i) for i in range(640))  # 不重复，重叠检测才不会误判
    _legacy_chunk(db, book, 0, page_text[:500])
    _legacy_chunk(db, book, 1, page_text[420:])
    db.commit()

    hits = rulebook_service.retrieve(db, "孤注一掷规则", "coc", k=3, embedder=FakeEmbedder())
    assert len(hits) == 1
    assert hits[0]["text"] == page_text
    assert hits[0]["section"] == ""


def test_新旧索引并存时都能检索(db_factory, monkeypatch):
    db = db_factory()
    old = _book(db, "旧书")
    old.status, old.embed_model = "ready", "fake-test"
    _legacy_chunk(db, old, 0, "【理智检定】遭遇恐怖事物时进行理智检定，失败会损失理智值。")
    db.commit()
    monkeypatch.setattr(rulebook_service, "parse_pdf", _canned(("理智损失", "理智损失五点以上要进行智力检定，成功则陷入临时性疯狂。", 3)))
    rulebook_service.ingest_rulebook(db, _book(db, "新书"), b"x", embedder=FakeEmbedder())

    hits = rulebook_service.retrieve(db, "理智检定 理智损失", "coc", k=3, embedder=FakeEmbedder())
    assert {bool(h["section"]) for h in hits} == {True, False}


def test_retrieve_empty_when_no_rulebook(db_factory):
    db = db_factory()
    assert rulebook_service.retrieve(db, "任何问题", "coc", embedder=FakeEmbedder()) == []


def test_retrieve_filters_by_rule_system(db_factory, monkeypatch):
    db = db_factory()
    monkeypatch.setattr(rulebook_service, "parse_pdf", _canned(("专属规则", "这是龙与地下城的专属规则内容。", 1)))
    rulebook_service.ingest_rulebook(db, _book(db, "DnD书", "dnd"), b"x", embedder=FakeEmbedder())
    # 按 coc 检索拿不到 dnd 的内容
    assert rulebook_service.retrieve(db, "规则", "coc", embedder=FakeEmbedder()) == []
    assert rulebook_service.retrieve(db, "规则", "dnd", embedder=FakeEmbedder())


def test_delete_rulebook_removes_sections_chunks_and_source(db_factory, monkeypatch):
    db = db_factory()
    monkeypatch.setattr(rulebook_service, "parse_pdf", _canned(("待删", "需要被删除的规则书内容。", 1)))
    book = _book(db, "待删")
    rulebook_service.save_source(book, b"%PDF-src")
    rulebook_service.ingest_rulebook(db, book, b"x", embedder=FakeEmbedder())
    source = rulebook_service.RULEBOOKS_DIR / book.source_file
    assert source.read_bytes() == b"%PDF-src"
    assert db.query(RuleChunk).count() == 1

    assert rulebook_service.delete_rulebook(db, book.id) is True
    assert db.query(RuleChunk).count() == 0
    assert db.query(RuleSection).count() == 0
    assert db.get(Rulebook, book.id) is None
    assert not source.exists()
    assert rulebook_service.delete_rulebook(db, "不存在") is False


def test_升级重建失败时保留旧索引(db_factory, monkeypatch):
    db = db_factory()
    monkeypatch.setattr(rulebook_service, "parse_pdf", _canned(("孤注一掷", "第二次也是最后一次机会。", 2)))
    book = _book(db)
    rulebook_service.ingest_rulebook(db, book, b"x", embedder=FakeEmbedder())

    def boom(_b):
        raise ValueError("坏 PDF")

    monkeypatch.setattr(rulebook_service, "parse_pdf", boom)
    rulebook_service.ingest_rulebook(db, book, b"x", embedder=FakeEmbedder(), keep_on_failure=True)
    assert book.status == "ready" and "坏 PDF" in book.error
    assert db.query(RuleChunk).count() == 1
    assert rulebook_service.retrieve(db, "孤注一掷", "coc", embedder=FakeEmbedder())


def test_切块逻辑落后且有原件的规则书会被列为待重建(db_factory, monkeypatch):
    db = db_factory()
    monkeypatch.setattr(rulebook_service, "parse_pdf", _canned(("行动点", "每一轮都能获得行动点。", 1)))
    with_source, without = _book(db, "有原件"), _book(db, "无原件")
    for b in (with_source, without):
        b.status, b.chunker_version = "ready", 0
    rulebook_service.save_source(with_source, b"%PDF")
    db.commit()
    assert rulebook_service.outdated_rulebooks(db) == [with_source]

    rulebook_service.ingest_rulebook(db, with_source, b"%PDF", embedder=FakeEmbedder(), keep_on_failure=True)
    assert rulebook_service.outdated_rulebooks(db) == []


# ---------- 小节标题命中与语义排序融合 ----------

def _order(query, texts, dense_order):
    """texts 里形如「【标题】正文」的取标题；dense_order 为语义排序（从好到差）。返回 (下标, 是否标题命中)。"""
    titles = rulebook_service._legacy_titles(texts)
    dense = [(i, 0.9 - n * 0.01) for n, i in enumerate(dense_order)]
    return [(i, m) for i, _s, _r, m in rulebook_service._fuse(query, titles, dense)]


def test_查询与小节标题完全相同时该节排最前():
    texts = [
        "技能条目里提到孤注一掷的例子",
        "孤注一掷失败的范例结果",
        "【在追逐中孤注一掷】追逐中不能孤注一掷",
        "【5.3 孤注一掷】引文……孤注一掷为玩家提供第二次机会",
    ]
    out = _order("孤注一掷", texts, [0, 1, 2, 3])
    assert out[0] == (3, True)          # 定义节语义排最后，仍排第一
    assert out[1] == (2, True)          # 标题包含查询的次之
    assert [i for i, _ in out[2:]] == [0, 1]


def test_问句外壳剥掉后按完全命中():
    texts = ["别的段落", "【孤注一掷】定义"]
    for q in ("孤注一掷是什么", "怎么进行孤注一掷", "孤注一掷的规则"):
        assert _order(q, texts, [0, 1])[0] == (1, True)


def test_没有标题命中时与纯语义排序完全相同():
    texts = ["【瞄准（奖励骰）】……", "【生命值】……", "普通段落"]
    out = _order("生命值降到0会发生什么", texts, [2, 0, 1])
    assert out == [(2, False), (0, False), (1, False)]


def test_只在括号附注里命中算弱命中():
    texts = ["【瞄准（奖励骰）】讲瞄准", "【奖励骰与惩罚骰】讲奖励骰"]
    out = _order("奖励骰", texts, [0, 1])
    assert out[0] == (1, True)
    assert out[1] == (0, True)


def test_页眉不算小节标题():
    header = "【第四章 技能】"
    texts = [header + "页一", header + "页二", header + "页三", header + "页四", "正文"]
    out = _order("技能", texts, [4, 0, 1, 2, 3])
    assert all(not m for _, m in out)   # 每页重复的「标题」是页眉
    assert out[0] == (4, False)


def test_新版词条多处共用的标题不算页眉(db_factory, monkeypatch):
    """「孤注一掷」是 4.4、5.3、9.3.4 等多个词条共用的真标题：新版词条的页眉已在解析时去掉，不能被误伤。"""
    monkeypatch.setattr(rulebook_service, "parse_pdf", _canned(*[
        ("孤注一掷", f"第{i}处关于孤注一掷的说明。", i) for i in range(1, 6)
    ], ("孤注一掷：失败", "孤注一掷失败让守秘人可以自由支配后果。", 9)))
    db = db_factory()
    rulebook_service.ingest_rulebook(db, _book(db), b"x", embedder=FakeEmbedder())
    hits = rulebook_service.retrieve(db, "孤注一掷", "coc", k=3, embedder=FakeEmbedder())
    assert all(h["section"].endswith("› 孤注一掷") for h in hits)
