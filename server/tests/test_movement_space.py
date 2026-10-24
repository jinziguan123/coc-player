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
