"""RAG 调用统计：累计 record 的四象限计数/命中率/平均分，及 summarize 汇总。纯函数、不调 LLM。

拆表后 record/summarize 直接操作「统计本体」（即 session_stats.rag_stats），不再包 world_state。
"""

from app.services import rag_stats


def test_record_accumulates_calls_hits_and_top_score():
    st = rag_stats.record({}, kind="rule", mode="active", query="战斗 伤害",
                          hits=[{"score": 0.8}, {"score": 0.6}])
    st = rag_stats.record(st, kind="rule", mode="active", query="理智",
                          hits=[{"score": 0.4}])
    assert st["totals"]["calls"] == 2
    assert st["totals"]["hits"] == 3           # 2 + 1 命中行
    assert st["totals"]["empty"] == 0
    slot = st["by_kind_mode"]["rule:active"]
    assert slot["calls"] == 2 and slot["hits"] == 3
    assert slot["score_sum"] == 1.2            # top 分累计 0.8 + 0.4


def test_record_empty_hits_counts_as_empty():
    st = rag_stats.record({}, kind="module", mode="passive", query="门厅", hits=[])
    assert st["totals"]["calls"] == 1 and st["totals"]["hits"] == 0
    assert st["totals"]["empty"] == 1
    assert st["by_kind_mode"]["module:passive"]["empty"] == 1


def test_record_does_not_mutate_input():
    st0 = {"totals": {"calls": 0, "hits": 0, "empty": 0}, "by_kind_mode": {}, "recent": []}
    st1 = rag_stats.record(st0, kind="rule", mode="active", query="x", hits=[{"score": 0.5}])
    assert st0["totals"]["calls"] == 0         # 原引用不被改
    assert st1["totals"]["calls"] == 1


def test_recent_is_capped_and_newest_first():
    st = {}
    for i in range(rag_stats._MAX_SAMPLES + 5):
        st = rag_stats.record(st, kind="rule", mode="active", query=f"q{i}", hits=[{"score": 0.1}])
    recent = st["recent"]
    assert len(recent) == rag_stats._MAX_SAMPLES        # 滚动截断
    assert recent[0]["query"] == f"q{rag_stats._MAX_SAMPLES + 4}"  # 最新在前


def test_summarize_reports_hit_rate_and_avg_top_score():
    st = rag_stats.record({}, kind="module", mode="active", query="a", hits=[{"score": 1.0}])
    st = rag_stats.record(st, kind="module", mode="active", query="b", hits=[])   # 空命中
    out = rag_stats.summarize(st)
    assert out["totals"]["calls"] == 2 and out["totals"]["hit_rate"] == 0.5
    q = out["by_kind_mode"]["module:active"]
    assert q["calls"] == 2 and q["empty"] == 1
    assert q["hit_rate"] == 0.5
    assert q["avg_top_score"] == 1.0          # 仅按命中的那次算平均，空命中不摊薄


def test_summarize_empty_is_zeroed():
    out = rag_stats.summarize({})
    assert out["totals"] == {"calls": 0, "total_hits": 0, "empty": 0, "hit_rate": 0.0}
    assert out["by_kind_mode"] == {} and out["recent"] == []
