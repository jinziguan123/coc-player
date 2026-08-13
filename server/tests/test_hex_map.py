"""六边形沙盘 P-Hex-1 单测：坐标数学、落位修复的确定性/幂等、KP 空间语义注入（不调 LLM）。"""

from app.ai.context import build_kp_context
from app.models import Character, EventLog, GameSession, Module
from app.services import hex_map, module_service, session_service


# ── axial 坐标数学 ──


class TestAxialMath:
    def test_距离(self):
        assert hex_map.axial_distance((0, 0), (0, 0)) == 0
        assert hex_map.axial_distance((0, 0), (1, 0)) == 1
        assert hex_map.axial_distance((0, 0), (2, -1)) == 2
        assert hex_map.axial_distance((0, 0), (1, -2)) == 2   # 正北两格

    def test_八方位词(self):
        assert hex_map.direction_word((0, 0), (1, -2)) == "北"
        assert hex_map.direction_word((0, 0), (1, -1)) == "东北"
        assert hex_map.direction_word((0, 0), (1, 0)) == "东"
        assert hex_map.direction_word((0, 0), (0, 1)) == "东南"
        assert hex_map.direction_word((0, 0), (-1, 2)) == "南"
        assert hex_map.direction_word((0, 0), (-1, 1)) == "西南"
        assert hex_map.direction_word((0, 0), (-1, 0)) == "西"
        assert hex_map.direction_word((0, 0), (0, -1)) == "西北"
        assert hex_map.direction_word((0, 0), (0, 0)) == ""   # 同格

    def test_远近词分档(self):
        assert hex_map.distance_word(0) == "同处"
        assert hex_map.distance_word(1) == "紧邻"
        assert hex_map.distance_word(3) == "不远"
        assert hex_map.distance_word(6) == "有些路程"
        assert hex_map.distance_word(7) == "相当远"


# ── 落位修复（确定性、幂等、只补洞不推翻）──


def _chain(n=3, with_map=None):
    """a-b-c… 链式连通的场景组；with_map 给指定下标预置坐标。"""
    ids = [f"s{i}" for i in range(n)]
    scenes = []
    for i, sid in enumerate(ids):
        s = {"id": sid, "title": f"场景{i}", "kind": "location",
             "connections": [ids[i + 1]] if i + 1 < n else []}
        if with_map and i in with_map:
            s["map"] = with_map[i]
        scenes.append(s)
    return scenes


class TestEnsureSceneMaps:
    def test_空白模组全量落位且相连就近(self):
        scenes = _chain(4)
        assert hex_map.ensure_scene_maps(scenes) is True
        coords = [hex_map.scene_coord(s) for s in scenes]
        assert all(c is not None for c in coords)
        assert len(set(coords)) == 4                      # 不重叠
        for a, b in zip(coords, coords[1:]):
            assert hex_map.axial_distance(a, b) <= 2      # 相连的就近落位

    def test_幂等且合法提议保留(self):
        scenes = _chain(3, with_map={0: {"q": 5, "r": -3, "biome": "urban"}})
        hex_map.ensure_scene_maps(scenes)
        assert hex_map.scene_coord(scenes[0]) == (5, -3)  # LLM 提议不被推翻
        assert scenes[0]["map"]["biome"] == "urban"
        snapshot = [dict(s["map"]) for s in scenes]
        assert hex_map.ensure_scene_maps(scenes) is False  # 第二次无改动
        assert [dict(s["map"]) for s in scenes] == snapshot

    def test_坐标冲突后者重排(self):
        scenes = _chain(2, with_map={0: {"q": 0, "r": 0, "biome": "plain"},
                                     1: {"q": 0, "r": 0, "biome": "plain"}})
        hex_map.ensure_scene_maps(scenes)
        assert hex_map.scene_coord(scenes[0]) == (0, 0)   # 列表序先到先得
        assert hex_map.scene_coord(scenes[1]) != (0, 0)

    def test_确定性同输入同输出(self):
        a, b = _chain(5), _chain(5)
        hex_map.ensure_scene_maps(a)
        hex_map.ensure_scene_maps(b)
        assert [s["map"] for s in a] == [s["map"] for s in b]

    def test_chapter不落位且清除误给(self):
        scenes = [
            {"id": "ch", "title": "委托与准备", "kind": "chapter",
             "map": {"q": 9, "r": 9, "biome": "plain"}},
            {"id": "s0", "title": "老宅", "kind": "location", "connections": []},
        ]
        hex_map.ensure_scene_maps(scenes)
        assert "map" not in scenes[0]                     # chapter 的误给被清掉
        assert hex_map.scene_coord(scenes[1]) is not None

    def test_biome归一与非法值兜底(self):
        scenes = _chain(2, with_map={0: {"q": 0, "r": 0, "biome": "URBAN"},
                                     1: {"q": 1, "r": 0, "biome": "太空"}})
        hex_map.ensure_scene_maps(scenes)
        assert scenes[0]["map"]["biome"] == "urban"
        assert scenes[1]["map"]["biome"] == "plain"

    def test_非整数坐标视为缺失(self):
        scenes = _chain(1, with_map={0: {"q": "北", "r": 0, "biome": "plain"}})
        hex_map.ensure_scene_maps(scenes)
        assert hex_map.scene_coord(scenes[0]) is not None


class TestNormalizeMapNodes:
    def test_只保留场景之间与场景周围一圈的自动地貌(self):
        scenes = [
            {"id": "a", "kind": "location", "map": {"q": 0, "r": 0, "biome": "urban"}},
            {"id": "b", "kind": "location", "map": {"q": 2, "r": 0, "biome": "forest"}},
        ]
        nodes = [
            {"id": "a", "q": 0, "r": 0, "biome": "urban", "scene_id": "a"},
            {"id": "b", "q": 2, "r": 0, "biome": "forest", "scene_id": "b"},
            {"id": "terrain_1_0", "q": 1, "r": 0, "biome": "plain", "scene_id": None},
            {"id": "terrain_20_20", "q": 20, "r": 20, "biome": "plain", "scene_id": None},
        ]

        normalized = module_service._normalize_map_nodes(nodes, scenes)
        coords = {(node["q"], node["r"]): node for node in normalized}

        assert (1, 0) in coords
        assert (20, 20) not in coords
        assert len(coords) == 13  # 两个场景及其去重后的六邻居并集（中间格被两圈共用）


class TestNeighborLabel:
    def test_有坐标出方位标签(self):
        cur = {"map": {"q": 0, "r": 0, "biome": "urban"}}
        nb = {"map": {"q": 1, "r": -2, "biome": "forest"}}
        assert hex_map.neighbor_label(cur, nb) == "北・不远"

    def test_任一侧无坐标返回None(self):
        cur = {"map": {"q": 0, "r": 0, "biome": "urban"}}
        assert hex_map.neighbor_label(cur, {}) is None
        assert hex_map.neighbor_label({}, cur) is None

    def test_地貌中文名(self):
        assert hex_map.biome_label({"map": {"biome": "swamp"}}) == "沼泽"
        assert hex_map.biome_label({"map": {"biome": "road"}}) == "道路"
        assert hex_map.biome_label({}) is None


# ── KP 上下文空间语义注入 ──


def _fixture(with_map: bool):
    scenes = [
        {"id": "a", "title": "镇广场", "kind": "location", "connections": ["b"],
         "keywords": ["广场"]},
        {"id": "b", "title": "老教堂", "kind": "location", "connections": [],
         "keywords": ["教堂"]},
    ]
    if with_map:
        scenes[0]["map"] = {"q": 0, "r": 0, "biome": "urban"}
        scenes[1]["map"] = {"q": 1, "r": -2, "biome": "ruin"}
    module = Module(title="测试镇", rule_system="coc", description="", world_setting={},
                    scenes=scenes, npcs=[], clues=[], triggers=[], handouts=[])
    session = GameSession(module_id="m", status="active", current_scene_id="a",
                          world_state={"visited_scenes": ["a"]})
    pc = Character(name="调查员甲", rule_system="coc", is_player=True,
                   base_attributes={}, skills={}, system_data={})
    return module, session, pc


class TestContextInjection:
    def test_有坐标时连通段带方位与地貌(self):
        module, session, pc = _fixture(with_map=True)
        messages = build_kp_context(session, module, pc, [])
        sys_msg = messages[0]["content"]
        assert "老教堂（北・不远）" in sys_msg
        assert "叙述方向、来路、途经时以此为准" in sys_msg
        assert "【场景地貌】城镇" in sys_msg

    def test_无坐标时保持原有连通段(self):
        module, session, pc = _fixture(with_map=False)
        messages = build_kp_context(session, module, pc, [])
        sys_msg = messages[0]["content"]
        assert "由此可直达：老教堂" in sys_msg
        assert "（北・" not in sys_msg
        assert "【场景地貌】" not in sys_msg


class TestKnownLocationsPayload:
    def test_map字段随已知场景下发(self):
        module, session, pc = _fixture(with_map=True)
        out = session_service.list_known_locations(module, session)
        cur = next(x for x in out if x["id"] == "a")
        assert cur["map"] == {"q": 0, "r": 0, "biome": "urban"}
        assert all("map" in x for x in out)


# ── KP 上帝视角（reveal_all）──


def _three_scene_fixture():
    """a 已访问；b 与 a 相连但未提及（未知）；c 孤立未知。"""
    scenes = [
        {"id": "a", "title": "门厅", "kind": "location", "connections": ["b"],
         "map": {"q": 0, "r": 0, "biome": "interior"}},
        {"id": "b", "title": "地窖", "kind": "location", "connections": [],
         "map": {"q": 1, "r": 0, "biome": "interior"}},
        {"id": "c", "title": "后山", "kind": "location", "connections": [],
         "map": {"q": 4, "r": -2, "biome": "mountain"}},
        {"id": "ch", "title": "尾声", "kind": "chapter"},
    ]
    module = Module(title="M", rule_system="coc", description="", world_setting={},
                    scenes=scenes, npcs=[], clues=[], triggers=[], handouts=[])
    session = GameSession(module_id="m", status="active", current_scene_id="a",
                          world_state={"visited_scenes": ["a"]})
    return module, session


class TestRevealAll:
    def test_玩家侧迷雾不变且known恒真(self):
        module, session = _three_scene_fixture()
        out = session_service.list_known_locations(module, session)
        ids = {x["id"] for x in out}
        assert ids == {"a"}                        # b/c 未知、ch 是章节 → 都不可见
        assert all(x["known"] for x in out)

    def test_KP上帝视角全场景带known标记(self):
        module, session = _three_scene_fixture()
        out = session_service.list_known_locations(module, session, reveal_all=True)
        by_id = {x["id"]: x for x in out}
        assert set(by_id) == {"a", "b", "c"}       # 章节仍不上图
        assert by_id["a"]["known"] is True
        assert by_id["b"]["known"] is False and by_id["c"]["known"] is False
        assert by_id["a"]["connections"] == ["b"]  # KP 侧拓扑完整（不受迷雾过滤）

    def test_未发现场景保留地貌格但隐藏剧情token(self):
        module, session = _three_scene_fixture()
        module.map_nodes = [
            {"id": "a", "q": 0, "r": 0, "biome": "urban", "scene_id": "a"},
            {"id": "b", "q": 2, "r": 0, "biome": "forest", "scene_id": "b"},
            {"id": "terrain_1_0", "q": 1, "r": 0, "biome": "forest", "scene_id": None},
        ]

        visible = session_service.list_visible_map_nodes(module, [{"id": "a"}])
        by_id = {node["id"]: node for node in visible}
        assert by_id["b"]["scene_id"] is None
        assert (by_id["b"]["q"], by_id["b"]["r"], by_id["b"]["biome"]) == (2, 0, "forest")
        assert by_id["terrain_1_0"]["scene_id"] is None

        god_visible = session_service.list_visible_map_nodes(module, [{"id": "a"}, {"id": "b"}], reveal_all=True)
        assert next(node for node in god_visible if node["id"] == "b")["scene_id"] == "b"


# ── KP 拖拽落位（set_scene_map）──


class _FakeDb:
    def add(self, obj):
        pass

    def commit(self):
        pass


class TestSetSceneMap:
    def test_移动成功且落新格(self):
        module, _ = _three_scene_fixture()
        new_map = hex_map.set_scene_map(_FakeDb(), module, "b", 2, -1)
        assert new_map == {"q": 2, "r": -1, "biome": "interior"}   # 未给 biome → 保留旧值
        assert next(s for s in module.scenes if s["id"] == "b")["map"]["q"] == 2

    def test_撞格与非法输入拒绝(self):
        import pytest

        module, _ = _three_scene_fixture()
        with pytest.raises(ValueError, match="已被"):
            hex_map.set_scene_map(_FakeDb(), module, "b", 0, 0)     # a 占着 (0,0)
        with pytest.raises(ValueError, match="章节"):
            hex_map.set_scene_map(_FakeDb(), module, "ch", 9, 9)
        with pytest.raises(ValueError, match="不存在"):
            hex_map.set_scene_map(_FakeDb(), module, "nope", 9, 9)
        with pytest.raises(ValueError, match="未知地貌"):
            hex_map.set_scene_map(_FakeDb(), module, "b", 9, 9, biome="太空")

    def test_顺带改地貌(self):
        module, _ = _three_scene_fixture()
        new_map = hex_map.set_scene_map(_FakeDb(), module, "c", 5, -2, biome="ruin")
        assert new_map["biome"] == "ruin"


# ── 层级归组（P-Hex-5）──
#
# 归组只决定「挂在谁下面、什么时候可见」，不发明任何几何：子沙盘的节点全是模组自己
# 写出来的场景。模组没写内部结构的地点，就没有子沙盘（那条红线仍然立着）。


def _loc(sid, biome, conns=(), title=None):
    return {
        "id": sid, "title": title or sid, "kind": "location",
        "map": {"biome": biome}, "connections": list(conns),
    }


class TestSceneHierarchy:
    def test_室内场景归到唯一的非室内邻居下(self):
        scenes = [
            _loc("village", "ruin", ["hut", "shrine"]),
            _loc("hut", "interior", ["village"]),
            _loc("shrine", "interior", ["village"]),
            _loc("road", "road", ["village"]),
        ]
        assert hex_map.infer_scene_parents(scenes) is True
        by = {s["id"]: s for s in scenes}
        assert hex_map.scene_parent(by["hut"]) == "village"
        assert hex_map.scene_parent(by["shrine"]) == "village"
        assert hex_map.scene_parent(by["village"]) == ""   # 顶层不动
        assert hex_map.scene_parent(by["road"]) == ""

    def test_chapter_不参与候选否则制造假歧义(self):
        """闇暗山的「最里面的小屋」正因候选里混进 chapter「逃离大火」才判不出唯一父级。"""
        scenes = [
            _loc("village", "ruin", ["inner"]),
            _loc("inner", "interior", ["village", "fire"]),
            _loc("road", "road", ["village"]),
            {"id": "fire", "title": "逃离大火", "kind": "chapter", "connections": ["inner"]},
        ]
        hex_map.infer_scene_parents(scenes)
        by = {s["id"]: s for s in scenes}
        assert hex_map.scene_parent(by["inner"]) == "village"

    def test_层级可超过两级(self):
        """室内套室内（农场 > 公寓楼 > 地牢）：等父级定下来后再顺着室内邻居往下认一层。"""
        scenes = [
            _loc("farm", "plain", ["flat"]),
            _loc("flat", "interior", ["farm", "dungeon"]),
            _loc("dungeon", "interior", ["flat"]),
            _loc("road", "road", ["farm"]),
        ]
        hex_map.infer_scene_parents(scenes)
        by = {s["id"]: s for s in scenes}
        assert hex_map.scene_parent(by["flat"]) == "farm"
        assert hex_map.scene_parent(by["dungeon"]) == "flat"

    def test_多个非室内邻居时歧义不猜(self):
        scenes = [
            _loc("village", "ruin", ["hut"]),
            _loc("camp", "plain", ["hut"]),
            _loc("hut", "interior", ["village", "camp"]),
        ]
        hex_map.infer_scene_parents(scenes)
        assert hex_map.scene_parent({"map": {}}) == ""
        by = {s["id"]: s for s in scenes}
        assert hex_map.scene_parent(by["hut"]) == ""   # 留在顶层，等 LLM 补全或人工归组

    def test_顶层会被掏空时整批放弃(self):
        """常暗之箱：整模组 7 个车厢全是 interior、互相串联、无任何外部连接。"""
        scenes = [_loc(f"car{i}", "interior", [f"car{i + 1}"]) for i in range(6)]
        scenes.append(_loc("car6", "interior", []))
        assert hex_map.infer_scene_parents(scenes) is False
        assert all(hex_map.scene_parent(s) == "" for s in scenes)

    def test_不成环(self):
        scenes = [
            _loc("a", "interior", ["b"]),
            _loc("b", "interior", ["a"]),
            _loc("out", "plain", ["a"]),
            _loc("road", "road", ["out"]),
        ]
        hex_map.infer_scene_parents(scenes)
        by = {s["id"]: s for s in scenes}
        # a 认 out 为父；b 只能认 a——反过来 a 认 b 会成环，必须被挡住
        assert hex_map.scene_parent(by["a"]) == "out"
        assert hex_map.scene_parent(by["b"]) == "a"

    def test_已有父级不被推翻(self):
        """KP 手动归过组的一律保留，推断只补空的。"""
        scenes = [
            _loc("village", "ruin", ["hut"]),
            _loc("camp", "plain", ["hut"]),
            _loc("hut", "interior", ["village", "camp"]),
        ]
        scenes[2]["map"]["parent"] = "camp"
        hex_map.infer_scene_parents(scenes)
        assert hex_map.scene_parent(scenes[2]) == "camp"

    def test_分层落位互不撞格且保住parent(self):
        """各层用各自的坐标空间；重写 map 时 parent 必须原样带上，否则每跑一次就抹平一次。"""
        scenes = [
            _loc("village", "ruin", ["hut", "shrine"]),
            _loc("hut", "interior", ["village"]),
            _loc("shrine", "interior", ["village"]),
            _loc("road", "road", ["village"]),
        ]
        hex_map.infer_scene_parents(scenes)
        hex_map.ensure_scene_maps(scenes)
        by = {s["id"]: s for s in scenes}
        assert hex_map.scene_parent(by["hut"]) == "village"      # 落位没把 parent 洗掉
        assert hex_map.scene_coord(by["hut"]) != hex_map.scene_coord(by["shrine"])
        # 幂等：再跑一遍不动任何东西
        assert hex_map.infer_scene_parents(scenes) is False
        assert hex_map.ensure_scene_maps(scenes) is False

    def test_换层后重新落位不继承旧坐标(self):
        """顶层排出的坐标搬进子沙盘只是无主残值，留着子沙盘会继承顶层那份散乱。"""
        scenes = [
            _loc("village", "ruin", ["hut"]),
            _loc("hut", "interior", ["village"]),
            _loc("road", "road", ["village"]),
        ]
        hex_map.ensure_scene_maps(scenes)               # 先按老规矩全平铺
        by = {s["id"]: s for s in scenes}
        by["hut"]["map"].update({"q": 9, "r": 9})       # 一个远在天边的顶层坐标
        hex_map.infer_scene_parents(scenes)
        hex_map.ensure_scene_maps(scenes)
        coord = hex_map.scene_coord(by["hut"])
        assert coord != (9, 9)                            # 旧的顶层坐标没被继承
        assert coord != (0, 0)                            # 原点留给父级本人
        assert hex_map.axial_distance(coord, (0, 0)) <= 3  # 就近围着父级铺开

    def test_父级未访问前子级对玩家不可见(self):
        """门禁：开局就不该知道村里有几间屋子；KP 上帝视角仍看得见，只是 known=False。

        注意「已知」本身不靠连通解锁（见 known_scene_ids：只认已访问与旁白提及），
        所以这里先用一条提到小屋的旁白把它变成已知，再单独验证门禁那一层的效果。
        """
        module = Module(
            title="村", rule_system="coc",
            scenes=[
                _loc("village", "ruin", ["hut", "road"], title="村庄遗址"),
                _loc("hut", "interior", ["village"], title="村民小屋"),
                _loc("road", "road", ["village"], title="山路"),
            ],
        )
        scenes = [dict(s) for s in module.scenes]
        hex_map.infer_scene_parents(scenes)
        hex_map.ensure_scene_maps(scenes)
        module.scenes = scenes
        assert hex_map.scene_parent(scenes[1]) == "village"

        mention = [EventLog(
            session_id="s", sequence_num=1, event_type="narration",
            content="远处能看见村民小屋的轮廓。", visibility=[],
        )]

        session = GameSession(
            module_id="m", status="active", current_scene_id="road",
            world_state={"visited_scenes": ["road"]},
        )
        ids = {x["id"] for x in session_service.list_known_locations(module, session, events=mention)}
        assert "hut" not in ids            # 旁白提过，但还没进村 → 仍不可见

        session.current_scene_id = "village"
        session.world_state = {"visited_scenes": ["road", "village"]}
        ids = {x["id"] for x in session_service.list_known_locations(module, session, events=mention)}
        assert "hut" in ids                # 进了村 → 子沙盘解锁

        # KP 上帝视角：全都看得见，靠 known 标记区分玩家知不知道
        session.world_state = {"visited_scenes": ["road"]}
        session.current_scene_id = "road"
        god = {
            x["id"]: x for x in
            session_service.list_known_locations(module, session, events=mention, reveal_all=True)
        }
        assert "hut" in god and god["hut"]["known"] is False

    def test_map_nodes_坐标跟着重排同步(self):
        """map_nodes 是坐标的第二份拷贝（模组详情页沙盘直接读它），归组后必须跟着重排；
        不同步就会出现「数据已归组，详情页却把子级摊在子沙盘四个角上」。"""
        class _M:
            scenes = [
                _loc("village", "ruin", ["hut"]),
                _loc("hut", "interior", ["village"]),
                _loc("road", "road", ["village"]),
            ]
            map_nodes = [
                {"id": "hut", "scene_id": "hut", "q": 9, "r": 9, "biome": "plain"},
                {"id": "t1", "q": 3, "r": 3, "biome": "forest"},   # 地貌节点：不该被动
            ]
        m = _M()
        scenes = [dict(s) for s in m.scenes]
        hex_map.infer_scene_parents(scenes)
        hex_map.ensure_scene_maps(scenes)
        nodes = {n["id"]: n for n in hex_map._synced_map_nodes(m, scenes)}
        by = {s["id"]: s for s in scenes}
        assert (nodes["hut"]["q"], nodes["hut"]["r"]) == hex_map.scene_coord(by["hut"])
        assert nodes["hut"]["biome"] == "interior"
        assert (nodes["t1"]["q"], nodes["t1"]["r"]) == (3, 3)   # 地貌节点原样保留
        # 坐标进了子层，层级也得一起进：玩家还没发现这个场景时它会被清掉 scene_id 当地貌格
        # 下发，那时节点自己身上的 parent 是唯一的层级来源，缺了就拿着子层坐标画进顶层。
        assert nodes["hut"]["parent"] == "village"
        assert "parent" not in nodes["t1"]           # 地貌节点恒属顶层

    def test_scenes没变但节点缺层级时仍要落库(self):
        """回归：改动判定原先只看 scenes。归组早就跑完、节点上却漏了 parent 的存量模组，
        两个修复器都说「没改动」，同步永远轮不到——《鬼屋》的街区内景就一直拿着子层坐标
        画在顶层，正好压住疗养院那一格，把它盖成点不动的空地。"""
        class _M:
            scenes = [
                _loc("village", "ruin", ["hut"]),
                _loc("hut", "interior", ["village"]),
                _loc("road", "road", ["village"]),
            ]
            map_nodes: list = []

        class _DB:
            def __init__(self): self.commits = 0
            def add(self, _v): pass
            def commit(self): self.commits += 1

        m, db = _M(), _DB()
        m.scenes = [dict(s) for s in m.scenes]
        hex_map.infer_scene_parents(m.scenes)
        hex_map.ensure_scene_maps(m.scenes)          # scenes 先修到位：此后它不再变
        m.map_nodes = [
            {"id": s["id"], "scene_id": s["id"], **{k: s["map"][k] for k in ("q", "r", "biome")}}
            for s in m.scenes
        ]                                             # 节点有坐标、独缺 parent
        assert hex_map.ensure_module_map(db, m) is True
        assert {n["id"]: n.get("parent") for n in m.map_nodes} == {
            "village": None, "hut": "village", "road": None,
        }
        assert hex_map.ensure_module_map(db, m) is False   # 幂等：补完就不再落库
        assert db.commits == 1

    def test_场景移回顶层时清掉节点上的旧层级(self):
        """否则它会永远卡在一个已经不存在的子沙盘里，顶层再也看不到这一格。"""
        class _M:
            scenes = [_loc("hut", "interior", [])]
            map_nodes = [{"id": "hut", "scene_id": "hut", "q": 1, "r": 1,
                          "biome": "interior", "parent": "村庄遗址"}]
        m = _M()
        scenes = [dict(s) for s in m.scenes]
        hex_map.ensure_scene_maps(scenes)            # 无邻居可挂 → 留在顶层
        nodes = {n["id"]: n for n in hex_map._synced_map_nodes(m, scenes)}
        assert "parent" not in nodes["hut"]

    def test_子沙盘留出原点给父级(self):
        """模组里的连通几乎总是星形（四间屋子各自只连村庄、彼此不相连）。父级不在场，
        星形就没有中心，子沙盘一条连线都画不出来。落位为此空出原点。"""
        scenes = [
            _loc("village", "ruin", ["a", "b", "c"]),
            _loc("a", "interior", ["village"]),
            _loc("b", "interior", ["village"]),
            _loc("c", "interior", ["village"]),
            _loc("road", "road", ["village"]),
        ]
        hex_map.infer_scene_parents(scenes)
        hex_map.ensure_scene_maps(scenes)
        by = {s["id"]: s for s in scenes}
        kids = [hex_map.scene_coord(by[i]) for i in ("a", "b", "c")]
        assert (0, 0) not in kids                                   # 原点是父级的
        assert all(hex_map.axial_distance(c, (0, 0)) <= 3 for c in kids)  # 围着原点，不排成链
