"""交错位置变换的独立契约，不加载回合或 AI 编排。"""
from app.application.narration_marks import remap_marks_after_rewrite, snap_offset


def test_改写后对话分组和工具事件保持相对顺序且落在句界():
    result = ["甲。乙。丙。", "原始", [], [(1, "甲", "说话"), (8, "乙", "回答")], [(4, "分组")]]
    events = [(0, "先"), (5, "中"), (10, "后")]
    remap_marks_after_rewrite(result, "旧文旧文旧文旧文旧文", events)
    assert [item[1] for item in events] == ["先", "中", "后"]
    assert [item[0] for item in events] == sorted(item[0] for item in events)
    assert events[0][0] == 0 and events[-1][0] == len(result[0])
    for offset in [m[0] for m in result[3]] + [m[0] for m in result[4]]:
        assert result[0][offset - 1] == "。"
    assert result[1] == "原始"


def test_空原文清理旧位置并将事件放在开头():
    result = ["新文", "原文", [], [(2, "甲", "话")], [(2, "组")]]
    events = [(5, "事件")]
    remap_marks_after_rewrite(result, "", events)
    assert len(result) == 3 and events == [(0, "事件")]


def test_无句界时保持合法偏移():
    assert snap_offset("abcdef", -1) == 0
    assert snap_offset("abcdef", 100) == 6
    assert snap_offset("abcdef", 3) == 3
