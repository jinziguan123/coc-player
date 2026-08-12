"""从模组地图 grounding 出沙盘坐标：解析、名称匹配、坐标换算、候选图择优（全程打桩）。"""

import asyncio
import json

from app.services import hex_map, module_map_vision as mmv


def _scenes():
    return [
        {"id": "s1", "title": "村庄遗址", "kind": "location"},
        {"id": "s2", "title": "村中祠堂", "kind": "location"},
        {"id": "s3", "title": "吊桥", "kind": "location"},
        {"id": "ch", "title": "逃离大火", "kind": "chapter"},
    ]


class _Vision:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def supports_vision(self):
        return True

    async def complete_vision(self, prompt, images, max_tokens=None):
        self.calls += 1
        return self.replies.pop(0) if self.replies else "[]"


def test_解析JSON与ref_box两种格式():
    j = '```json\n[{"label":"祠堂","bbox_2d":[100,200,140,240]}]\n```'
    assert mmv.parse_grounding(j) == [{"label": "祠堂", "bbox": [100.0, 200.0, 140.0, 240.0]}]

    tagged = "<ref>吊桥</ref><box>(10,20),(30,40)</box>"
    assert mmv.parse_grounding(tagged) == [{"label": "吊桥", "bbox": [10.0, 20.0, 30.0, 40.0]}]

    assert mmv.parse_grounding("这不是地图") == []


def test_名称匹配容忍图上写简称():
    """地图上常写「祠堂」而模组里叫「村中祠堂」，反过来也有。"""
    det = [
        {"label": "祠堂", "bbox": [0, 0, 10, 10]},
        {"label": "村庄遗址", "bbox": [100, 0, 110, 10]},
        {"label": "某座无关的山", "bbox": [200, 0, 210, 10]},
    ]
    matched = mmv.match_labels(det, _scenes())
    assert [m[0] for m in matched] == ["s2", "s1"]      # 对不上的不硬凑


def test_chapter不参与匹配():
    det = [{"label": "逃离大火", "bbox": [0, 0, 10, 10]}]
    assert mmv.match_labels(det, _scenes()) == []       # 章节不是地点，不该上沙盘


def test_同一场景不被两个标签重复占用():
    det = [
        {"label": "村中祠堂", "bbox": [0, 0, 10, 10]},
        {"label": "祠堂", "bbox": [500, 500, 510, 510]},
    ]
    assert len(mmv.match_labels(det, _scenes())) == 1


def test_坐标换算保住图上的相对方位():
    """西边的地点在沙盘上也要在西边，北边的在北边——这是本功能唯一的判据。"""
    pts = [("w", 100.0, 500.0), ("e", 900.0, 500.0), ("n", 500.0, 100.0), ("s", 500.0, 900.0)]
    coords = {p["id"]: (p["q"], p["r"]) for p in mmv.detections_to_axial(pts)}
    # 屏幕东 = +q 方向；用 hexXY 的 x 分量比较，避开 axial 的斜置带来的直觉陷阱
    def px(sid):
        q, r = coords[sid]
        return q + r / 2
    assert px("w") < px("e")
    assert coords["n"][1] < coords["s"][1]              # 图像 y 向下 = r 向南，不翻转


def test_坐标换算满足落位间距():
    pts = [(f"s{i}", float(i % 4) * 250, float(i // 4) * 250) for i in range(8)]
    coords = [(p["q"], p["r"]) for p in mmv.detections_to_axial(pts)]
    assert len(set(coords)) == len(coords)              # 不重叠
    assert all(
        hex_map.axial_distance(a, b) >= 2
        for i, a in enumerate(coords) for b in coords[i + 1:]
    )


def test_择优选出真正的地图那张():
    """插画在前、地图在后时，要选检出匹配最多的那张，而不是第一张。"""
    illustration = "这张图是一幅插画，没有地名。"
    real_map = json.dumps([
        {"label": "村庄遗址", "bbox_2d": [100, 100, 150, 150]},
        {"label": "村中祠堂", "bbox_2d": [400, 200, 450, 250]},
        {"label": "吊桥", "bbox_2d": [700, 600, 750, 650]},
    ], ensure_ascii=False)
    llm = _Vision([illustration, real_map])
    imgs = [(b"a", "image/jpeg"), (b"b", "image/jpeg")]
    got = asyncio.run(mmv.locate_scenes_on_map(imgs, _scenes(), llm=llm))
    assert got["index"] == 1 and got["matched"] == 3
    assert {p["id"] for p in got["proposals"]} == {"s1", "s2", "s3"}


def test_匹配太少时不据此摆沙盘():
    """一两个匹配可能只是插画里恰好写了个地名，据此摆整张沙盘还不如按文字猜。"""
    weak = json.dumps([{"label": "吊桥", "bbox_2d": [10, 10, 20, 20]}], ensure_ascii=False)
    got = asyncio.run(mmv.locate_scenes_on_map([(b"a", "image/jpeg")], _scenes(), llm=_Vision([weak])))
    assert got["proposals"] == []


def test_没有视觉模型时安静退出():
    class TextOnly:
        def supports_vision(self):
            return False

    got = asyncio.run(mmv.locate_scenes_on_map([(b"a", "image/jpeg")], _scenes(), llm=TextOnly()))
    assert got["proposals"] == [] and got["matched"] == 0


class _FastLLM:
    """假的文本快模型：按预设返回配对 JSON，并记录收到的输入。"""

    def __init__(self, reply):
        self.reply = reply
        self.payload = None

    async def complete(self, messages, **kw):
        self.payload = json.loads(messages[-1]["content"])
        return self.reply


def test_配对补上叫法不同的地点():
    """鬼屋实测：地图写「闹鬼的房子」而场景叫「科比特的老房子」，字符串匹配认不出。"""
    scenes = [
        {"id": "s1", "title": "科比特的老房子", "kind": "location"},
        {"id": "s2", "title": "委托与准备", "kind": "location"},
        {"id": "s3", "title": "中央图书馆", "kind": "location"},
    ]
    det = json.dumps([
        {"label": "闹鬼的房子", "bbox_2d": [100, 300, 160, 360]},
        {"label": "出发地", "bbox_2d": [800, 100, 860, 160]},
        {"label": "中央图书馆", "bbox_2d": [500, 300, 560, 360]},
    ], ensure_ascii=False)
    pair = _FastLLM(json.dumps({"pairs": [
        {"label": "闹鬼的房子", "id": "s1"},
        {"label": "出发地", "id": "s2"},
    ]}, ensure_ascii=False))

    got = asyncio.run(mmv.locate_scenes_on_map(
        [(b"m", "image/png")], scenes, llm=_Vision([det]), pair_llm=pair,
    ))
    assert got["matched"] == 3                                  # 1 个字符串 + 2 个配对
    assert {p["id"] for p in got["proposals"]} == {"s1", "s2", "s3"}
    # 只把「字符串没认出的」交给配对，不重复问已经确定的
    assert pair.payload["map_labels"] == ["闹鬼的房子", "出发地"]
    assert {s["id"] for s in pair.payload["scenes"]} == {"s1", "s2"}


def test_配对结果过确定性校验():
    """编造的 id、重复占用、没检出过的 label 一律丢弃——AI 提议仍要确定性收口。"""
    scenes = [{"id": "s1", "title": "甲", "kind": "location"}]
    bad = json.dumps({"pairs": [
        {"label": "某地", "id": "不存在的id"},
        {"label": "没检出过的标签", "id": "s1"},
    ]}, ensure_ascii=False)
    got = asyncio.run(mmv.pair_with_llm(["某地"], scenes, llm=_FastLLM(bad)))
    assert got == {}


def test_配对失败时退回字符串结果():
    class _Boom:
        async def complete(self, *a, **k):
            raise RuntimeError("boom")

    assert asyncio.run(mmv.pair_with_llm(["某地"], [{"id": "s1", "title": "甲"}], llm=_Boom())) == {}


def test_零字符串命中时也会试一次配对():
    """地图整套换了叫法（一个都没字面命中）→ 取检出最多的那张，交给配对判断。"""
    scenes = [
        {"id": "s1", "title": "旧宅", "kind": "location"},
        {"id": "s2", "title": "码头", "kind": "location"},
        {"id": "s3", "title": "灯塔", "kind": "location"},
    ]
    det = json.dumps([
        {"label": "凶宅", "bbox_2d": [100, 100, 160, 160]},
        {"label": "渔港", "bbox_2d": [500, 200, 560, 260]},
        {"label": "白塔", "bbox_2d": [800, 600, 860, 660]},
    ], ensure_ascii=False)
    pair = _FastLLM(json.dumps({"pairs": [
        {"label": "凶宅", "id": "s1"}, {"label": "渔港", "id": "s2"}, {"label": "白塔", "id": "s3"},
    ]}, ensure_ascii=False))
    got = asyncio.run(mmv.locate_scenes_on_map(
        [(b"m", "image/png")], scenes, llm=_Vision([det]), pair_llm=pair,
    ))
    assert got["matched"] == 3


def test_上传流程默认按地图落位(monkeypatch):
    """默认开：解析完在入库前把地图坐标写成提议，交给既有的确定性修复器收口。"""
    from app.api import modules as modules_api

    captured = {}

    async def fake_locate(images, scenes, llm=None, pair_llm=None):
        captured["scenes"] = [s["id"] for s in scenes]
        return {"index": 0, "matched": 2, "detections": [], "pairs": [],
                "proposals": [{"id": "s1", "q": 3, "r": -1}, {"id": "s2", "q": -2, "r": 2}]}

    monkeypatch.setattr(modules_api.module_map_vision, "locate_scenes_on_map", fake_locate)
    parsed = {"scenes": [{"id": "s1", "title": "甲"}, {"id": "s2", "title": "乙"}, {"id": "s3", "title": "丙"}]}
    job = modules_api._job_new()
    asyncio.run(modules_api._apply_map_grounding(job, parsed, [(b"m", "image/png")]))

    assert parsed["scenes"][0]["map"] == {"q": 3, "r": -1}
    assert parsed["scenes"][1]["map"] == {"q": -2, "r": 2}
    assert "map" not in parsed["scenes"][2]        # 没认出的不动，留给文字落位
    assert captured["scenes"] == ["s1", "s2", "s3"]

    # 写进去的提议要能被入库前的归一化原样继承（「合法提议保留」）
    from app.services import hex_map, module_service
    normalized = module_service._normalize_scenes([dict(s) for s in parsed["scenes"]])
    assert hex_map.scene_coord(normalized[0]) == (3, -1)
    assert hex_map.scene_coord(normalized[1]) == (-2, 2)
    assert hex_map.scene_coord(normalized[2]) is not None   # 第三个由修复器补


def test_地图定位失败不影响模组入库(monkeypatch):
    """整段 fail-open：沙盘落位是锦上添花，模组本身已经解析好了。"""
    from app.api import modules as modules_api

    async def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(modules_api.module_map_vision, "locate_scenes_on_map", boom)
    parsed = {"scenes": [{"id": "s1", "title": "甲"}]}
    asyncio.run(modules_api._apply_map_grounding(modules_api._job_new(), parsed, [(b"m", "image/png")]))
    assert "map" not in parsed["scenes"][0]        # 安静跳过，不抛
