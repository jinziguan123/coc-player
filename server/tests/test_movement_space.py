"""空间快照契约：解析不依赖 ORM，模糊出口不替玩家选方向。"""
import pytest

from app.application.movement_space import project_space
from app.domain.movement_language import analyze
from app.domain.movement_space import resolve_destination


def space():
    return project_space([
        {"id": "hall", "title": "前室", "description": "北侧拱门通往耳室。东侧拱门通往墓室。"},
        {"id": "north", "title": "耳室"},
        {"id": "east", "title": "墓室"},
    ], ["hall", "north", "east"], ["east", "north"])


@pytest.mark.parametrize("text,status,target", [
    ("我穿过拱门", "ambiguous", None),
    ("我从北侧拱门钻进去", "resolved", "north"),
    ("我穿过东侧拱门", "resolved", "east"),
    ("我进入耳室", "resolved", "north"),
    ("我跟着进去", "ambiguous", None),
])
def test_多出口按方位解析_泛指必须澄清(text, status, target):
    result = resolve_destination(space(), "hall", text)
    assert (result.status, result.target) == (status, target)


def test_显式出口别名与快照隔离():
    source = [
        {"id": "hall", "title": "前室", "exits": [
            {"to": "north", "name": "窄门", "aliases": ["雕花门"]},
        ]},
        {"id": "north", "title": "耳室"},
        {"id": "east", "title": "墓室"},
    ]
    snapshot = project_space(source, ["hall", "north", "east"], ["east", "north"])
    source[0]["exits"][0]["to"] = "east"
    source[0]["exits"][0]["aliases"].clear()
    assert resolve_destination(snapshot, "hall", "我穿过雕花门").target == "north"


def test_没有连通候选时不能凭空解释出口():
    snapshot = project_space([{"id": "hall", "title": "前室"}], ["hall"], [])
    assert resolve_destination(snapshot, "hall", "我穿过拱门").status == "unresolved"


@pytest.mark.parametrize("text,kind,solo", [
    ("我抛下队友，从拱门钻进去", "act", True),
    ("我要从拱门钻进去", "act", False),
    ("我们要不要进去？", "discuss", False),
    ("如果门打开，我就进去", "conditional", False),
    ("我走近拱门观察", "approach", False),
])
def test_原话分类独立于存档与模型(text, kind, solo):
    result = analyze(text)
    assert (result.kind, result.solo) == (kind, solo)


@pytest.mark.parametrize(("text", "kind", "acted"), [
    # 真实模型验收 s2：留守者叫别人先走，自己的原话不算行动
    ("我留在甬道守着出口，你们先进去", "negated", []),
    ("你们先进去，我留下守着", "none", []),
    # 含第一人称的第二人称句仍是说话人自己的行动
    ("你们跟我进去", "act", ["你们跟我进去"]),
    ("你们留在这，我钻进去", "act", ["我钻进去"]),
    # 真实模型验收 s6：「钻回」等返回动作
    ("我从墙洞钻回甬道", "act", ["我从墙洞钻回甬道"]),
    ("我爬回洞口那边", "act", ["我爬回洞口那边"]),
])
def test_第二人称叫别人走不算自己行动_返回动作算行动(text, kind, acted):
    result = analyze(text)
    assert (result.kind, result.sentences) == (kind, acted)


def test_行动分句所在整句记为去向解析的退路():
    result = analyze("我用肩膀顶住东侧拱门前的石板，使劲把它推开，然后钻进去。灯给你")
    assert result.sentences == ["然后钻进去"]
    assert result.context == ["我用肩膀顶住东侧拱门前的石板，使劲把它推开，然后钻进去"]
