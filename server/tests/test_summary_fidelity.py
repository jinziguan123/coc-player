"""摘要提示词的保真约束：滚动摘要与章节合并都要求专名/数值原样保留、不提压缩本身（不调真实模型）。"""

from types import SimpleNamespace

from app.ai.story_summarizer import build_memory_keeper_messages, build_merge_messages


def _prompt(messages: list[dict]) -> str:
    return "\n".join(m["content"] for m in messages)


def _ev(content: str, etype: str = "narration", who: str = ""):
    return SimpleNamespace(event_type=etype, actor_name=who, content=content)


def _assert_fidelity(text: str) -> None:
    for kw in ("人名", "地名", "物品名", "已发现线索", "数值", "一字不差"):
        assert kw in text, kw
    assert "不要提及「摘要」「合并」「压缩」" in text
    assert "不推测" in text


def test_rolling_summary_prompt_requires_verbatim_facts():
    text = _prompt(build_memory_keeper_messages(
        "调查员抵达阿卡姆。", [_ev("管家说钥匙在第三个抽屉。", "dialogue", "管家")], "",
    ))
    _assert_fidelity(text)
    # 线索不展开细节，但要按原名点到——旧措辞「不必逐条保留线索细节」与保真冲突，已去掉
    assert "不必逐条保留线索细节" not in text
    assert "按原名点到" in text


def test_chapter_merge_prompt_unions_facts_and_keeps_verbatim():
    text = _prompt(build_merge_messages(["第一章：见到了老韦伯。", "第二章：老韦伯撒了谎。"]))
    _assert_fidelity(text)
    assert "取并集" in text
    assert "以后一段为准" in text
