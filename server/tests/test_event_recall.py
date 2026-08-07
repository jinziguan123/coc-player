"""事件原文回捞：索引、检索、渲染，以及「有损缓存 + 无损底座」这个契约本身。

用假嵌入器（确定性哈希向量）跑，不加载真模型——这一层验的是索引/检索/窗口/降级的接线，
语义质量归 evals。
"""

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base
from app.models.character import Character
from app.models.event_log import EventLog
from app.models.module import Module
from app.models.session import GameSession
from app.services import event_recall


class _FakeEmbedder:
    """把文本映射到确定性向量：共享词越多越相近，够用来验检索接线。"""

    dim = 16
    model_name = "fake"

    def _vec(self, text: str) -> list[float]:
        v = np.zeros(self.dim, dtype=np.float64)
        for token in text:
            v[hash(token) % self.dim] += 1.0
        n = np.linalg.norm(v)
        return (v / n if n else v).tolist()

    def embed_passages(self, texts):
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'recall.db'}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


@pytest.fixture
def session(db):
    module = Module(title="回捞测试模组", rule_system="coc")
    player = Character(name="亨利", rule_system="coc")
    db.add_all([module, player])
    db.flush()
    gs = GameSession(module_id=module.id, player_character_id=player.id, status="active")
    db.add(gs)
    db.commit()
    return gs


def _add(db, session_id, seq, etype, content, actor="KP", scene=None):
    ev = EventLog(
        session_id=session_id, sequence_num=seq, event_type=etype,
        content=content, actor_name=actor, metadata_={"scene_id": scene} if scene else {},
    )
    db.add(ev)
    db.commit()
    return ev


# ── 索引 ────────────────────────────────────────────────────────────────


def test_只索引叙事类事件(db, session):
    _add(db, session.id, 1, "narration", "长廊尽头的烛火忽明忽暗，墙皮剥落。")
    _add(db, session.id, 2, "dice", "侦查 检定成功")           # 机械噪音，不索引
    _add(db, session.id, 3, "system", "场景切换到书房")          # 同上
    _add(db, session.id, 4, "dialogue", "当铺老板压低嗓子说了个数。")

    n = event_recall.index_pending(db, session.id, embedder=_FakeEmbedder())
    assert n == 2
    indexed = {e.sequence_num for e in db.query(EventLog).filter(
        EventLog.embedding.is_not(None)).all()}
    assert indexed == {1, 4}


def test_过短事件不索引(db, session):
    _add(db, session.id, 1, "action", "我搜查")                  # 短于阈值
    assert event_recall.index_pending(db, session.id, embedder=_FakeEmbedder()) == 0


def test_索引是增量的_不重复做功(db, session):
    _add(db, session.id, 1, "narration", "长廊尽头的烛火忽明忽暗，墙皮剥落。")
    assert event_recall.index_pending(db, session.id, embedder=_FakeEmbedder()) == 1
    assert event_recall.index_pending(db, session.id, embedder=_FakeEmbedder()) == 0
    _add(db, session.id, 2, "narration", "地窖的门在身后合上，插销落下的声音很闷。")
    assert event_recall.index_pending(db, session.id, embedder=_FakeEmbedder()) == 1


def test_嵌入器炸了不阻塞跑团(db, session):
    """索引是后台收尾动作，任何失败都不该影响这一局能不能继续玩。"""
    class _Boom:
        dim, model_name = 16, "boom"
        def embed_passages(self, texts):
            raise RuntimeError("模型加载失败")
        def embed_query(self, text):
            raise RuntimeError("模型加载失败")

    _add(db, session.id, 1, "narration", "长廊尽头的烛火忽明忽暗，墙皮剥落。")
    assert event_recall.index_pending(db, session.id, embedder=_Boom()) == 0


# ── 检索 ────────────────────────────────────────────────────────────────


def test_回捞带上下文窗口(db, session):
    """单条台词脱离上下文没有意义——命中一条要连同前后几条一起给。"""
    for i in range(1, 10):
        _add(db, session.id, i, "narration", f"第{i}段无关紧要的走廊描写，灯影摇晃。")
    _add(db, session.id, 10, "dialogue", "暗号是「北风起时，渡口见」，当铺老板说。", actor="当铺老板")
    for i in range(11, 16):
        _add(db, session.id, i, "narration", f"第{i}段无关紧要的走廊描写，灯影摇晃。")
    event_recall.index_pending(db, session.id, embedder=_FakeEmbedder())

    hits = event_recall.recall(db, session.id, "暗号 当铺老板 渡口",
                               k=1, embedder=_FakeEmbedder())
    assert hits
    seqs = [e.sequence_num for e in hits[0]["events"]]
    assert 10 in seqs                       # 命中那条在内
    assert len(seqs) > 1                    # 且带了邻近上下文
    assert seqs == sorted(seqs)             # 按时间正序，不打乱因果


def test_before_seq_只查游标之前(db, session):
    """游标之后的事件本来就在上下文里逐字躺着，再查一遍是浪费。"""
    for i in range(1, 8):
        _add(db, session.id, i, "narration", f"早期第{i}段：地窖里的血迹已经发黑。")
    for i in range(8, 14):
        _add(db, session.id, i, "narration", f"近期第{i}段：地窖里的血迹已经发黑。")
    event_recall.index_pending(db, session.id, embedder=_FakeEmbedder())

    hits = event_recall.recall(db, session.id, "地窖 血迹", k=3,
                               before_seq=8, embedder=_FakeEmbedder())
    assert hits
    assert all(e.sequence_num < 8 for h in hits for e in h["events"])


def test_窗口重叠不重复注入同一段(db, session):
    """相邻命中点的窗口会重叠，去重后同一条事件只出现一次。"""
    for i in range(1, 8):
        _add(db, session.id, i, "narration", f"第{i}段：地窖里的血迹已经发黑，气味刺鼻。")
    event_recall.index_pending(db, session.id, embedder=_FakeEmbedder())
    hits = event_recall.recall(db, session.id, "地窖 血迹", k=3, embedder=_FakeEmbedder())
    seen = [e.sequence_num for h in hits for e in h["events"]]
    assert len(seen) == len(set(seen))


def test_没有索引时返回空(db, session):
    _add(db, session.id, 1, "narration", "还没建过索引的一段旁白，灯影摇晃着。")
    assert event_recall.recall(db, session.id, "灯影", embedder=_FakeEmbedder()) == []


def test_空查询返回空(db, session):
    assert event_recall.recall(db, session.id, "  ", embedder=_FakeEmbedder()) == []


# ── 渲染与降级 ──────────────────────────────────────────────────────────


def test_渲染保留说话人(db, session):
    _add(db, session.id, 1, "dialogue", "北风起时，渡口见。", actor="当铺老板")
    _add(db, session.id, 2, "narration", "他说完就把当票推了回来，眼神躲闪。")
    event_recall.index_pending(db, session.id, embedder=_FakeEmbedder())
    hits = event_recall.recall(db, session.id, "渡口 当票", k=1, embedder=_FakeEmbedder())
    text = event_recall.format_recall(hits)
    assert "当铺老板：北风起时，渡口见。" in text
    assert "旁白：" in text


def test_查不到时给明确降级文案():
    """必须明说「没找到」——含糊其辞会让 KP 顺着编，那正是这个特性要防的。"""
    assert "没有找到" in event_recall.format_recall([])


# ── 能力广告的开关 ──────────────────────────────────────────────────────


def test_没浓缩过就不广告回想能力():
    """历史全在上下文里时广告它，只会诱导 KP 去查眼前就有的东西。"""
    assert event_recall.is_enabled(GameSession(world_state={})) is False
    assert event_recall.is_enabled(GameSession(world_state=None)) is False
    assert event_recall.is_enabled(GameSession(world_state={"story_summary_seq": 0})) is False


def test_浓缩过就广告():
    assert event_recall.is_enabled(GameSession(world_state={"story_summary_seq": 42})) is True
