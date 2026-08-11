"""六边形沙盘：axial 坐标数学 + 场景落位管线（AI 提议 → 确定性修复 → KP 修正）。

场景间地理位置存在 scene["map"] = {"q", "r", "biome"}（pointy-top axial：东为 +q，
南为 +r 的屏幕方向；正北落在 (q, r) 的 (+1, -2) 一线）。坐标是**象征性相对位置**：
只承诺方位与相对远近，不承诺比例尺；travel 仍走 connections 图校验，本模块不改变
移动规则。场景内部几何（房间/墙）是既定红线，勿在此扩展。

修复原则「只补洞、不推翻」：LLM 解析或 KP 拖拽给出的合法坐标一律保留；缺失/冲突的
场景按「已定位邻居重心就近 + 螺旋找空位」确定性落位（同输入同输出）。存量模组无
map 字段 → 同一修复器懒回填（ensure_module_map，幂等）。
"""

from __future__ import annotations

import math

BIOMES = (
    "plain", "forest", "water", "coast", "desert",
    "mountain", "swamp", "urban", "ruin", "interior", "road",
)
BIOME_LABELS = {
    "plain": "原野", "forest": "密林", "water": "水域", "coast": "海岸",
    "desert": "荒漠", "mountain": "山地", "swamp": "沼泽", "urban": "城镇",
    "ruin": "废墟", "interior": "室内", "road": "道路",
}

# pointy-top axial 的六个邻接方向（环绕一圈，顺序只需确定性）
_DIRS = ((1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1))

_DIR_WORDS = ("北", "东北", "东", "东南", "南", "西南", "西", "西北")


def axial_distance(a: tuple[int, int], b: tuple[int, int]) -> int:
    dq, dr = a[0] - b[0], a[1] - b[1]
    return (abs(dq) + abs(dr) + abs(dq + dr)) // 2


def _to_pixel(q: float, r: float) -> tuple[float, float]:
    """axial → 屏幕像素方向（x 向东，y 向南），只用于算方位角。"""
    return q + r / 2, r * math.sqrt(3) / 2


def direction_word(frm: tuple[int, int], to: tuple[int, int]) -> str:
    """八方位词：以正北为 0° 顺时针分扇区。同格返回空串。"""
    x0, y0 = _to_pixel(*frm)
    x1, y1 = _to_pixel(*to)
    dx, dy = x1 - x0, y1 - y0
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return ""
    bearing = math.degrees(math.atan2(dx, -dy)) % 360
    return _DIR_WORDS[round(bearing / 45) % 8]


def distance_word(d: int) -> str:
    if d <= 0:
        return "同处"
    if d == 1:
        return "紧邻"
    if d <= 3:
        return "不远"
    if d <= 6:
        return "有些路程"
    return "相当远"


def scene_coord(scene: dict | None) -> tuple[int, int] | None:
    """场景的合法坐标；缺失/非整数返回 None（fail-open）。"""
    m = (scene or {}).get("map")
    if not isinstance(m, dict):
        return None
    try:
        return int(m["q"]), int(m["r"])
    except (KeyError, TypeError, ValueError):
        return None


def scene_parent(scene: dict | None) -> str:
    """场景挂在哪个父级地点之下；空串 = 顶层沙盘（缺省，存量模组行为不变）。"""
    m = (scene or {}).get("map")
    if not isinstance(m, dict):
        return ""
    return str(m.get("parent") or "").strip()


# ── 层级归组 ────────────────────────────────────────────────────────────────
#
# 归组只做两件事：**这个地点挂在谁下面**、**什么时候可见**。它不发明任何几何——
# 子沙盘的节点全是模组自己写出来的场景，`connections` 图与移动规则一字不动。
# 「场景内部几何（房间/墙）」那条红线仍然立着：模组只写了「一间小屋」，就没有子沙盘。
#
# 之所以需要它：室内场景今天与山路平铺在同一层，被螺旋落位甩得到处都是（闇暗山的四间
# 屋子落在 (-1,6)(1,6)(3,5)(4,3)，而村庄遗址在 (2,4)），既看不出从属，也提前把
# 「村里有几间屋子」摊给了玩家。
#
#: 归组后顶层至少要剩下这么多个地点，否则整批放弃。
#: 反例是「常暗之箱」——整模组 7 个车厢全是 interior、互相串联、没有任何外部连接，
#: 若按「interior 一律下沉」处理，顶层沙盘会直接空掉。
MIN_TOP_LEVEL_SCENES = 2


def _walks_to_cycle(parent_of: dict[str, str], child: str, parent: str) -> bool:
    """把 parent 指派给 child 会不会成环（顺着父链往上走能否走回 child）。"""
    seen, cur = {child}, parent
    while cur:
        if cur in seen:
            return True
        seen.add(cur)
        cur = parent_of.get(cur, "")
    return False


def infer_scene_parents(scenes: list) -> bool:
    """确定性推断室内场景的父级地点（原地写 scene["map"]["parent"]）。返回是否有改动。

    判据只有一条、且完全来自模组自己写的 connections：**某个 interior 场景在连通图里
    只挨着一个「非 interior」邻居**，那个邻居就是它的父级。多于一个 = 歧义，不猜；
    一个都没有（内部套内部，如「农场 > 公寓楼 > 地牢」）则等父级先被定下来，再顺着
    interior 邻居往下认一层——所以层级天然可以超过两级，不必写死。

    chapter 场景先滤掉：它们不是地点、本就不上沙盘，留着只会制造假歧义（闇暗山的
    「最里面的小屋」正是因为候选里混进了 chapter「逃离大火」才判不出唯一父级）。

    已经有 parent 的一律保留（KP 手动归组过的不推翻），只补空的。
    """
    locs = [s for s in scenes or [] if isinstance(s, dict) and s.get("kind") != "chapter"]
    by_id = {str(s.get("id")): s for s in locs if s.get("id")}
    if not by_id:
        return False

    adj: dict[str, set[str]] = {i: set() for i in by_id}
    for sid, s in by_id.items():
        for c in s.get("connections") or []:
            other = str(c or "").strip()
            if other in adj and other != sid:
                adj[sid].add(other)
                adj[other].add(sid)

    def is_interior(sid: str) -> bool:
        m = by_id[sid].get("map")
        return isinstance(m, dict) and str(m.get("biome") or "").lower() == "interior"

    parent_of = {sid: scene_parent(s) for sid, s in by_id.items()}
    pending = [sid for sid in by_id if is_interior(sid) and not parent_of[sid]]

    # 逐轮收敛：每轮先认「唯一的非 interior 邻居」，再认「已经归好组的 interior 邻居」。
    # 一轮下来没有任何新指派就停——剩下的要么歧义、要么真的没有父级（如整列火车）。
    while pending:
        settled: list[str] = []
        for sid in pending:
            outer = [n for n in sorted(adj[sid]) if not is_interior(n)]
            if len(outer) != 1:
                outer = [n for n in sorted(adj[sid]) if is_interior(n) and parent_of.get(n)]
                if len(outer) != 1:
                    continue
            if _walks_to_cycle(parent_of, sid, outer[0]):
                continue
            parent_of[sid] = outer[0]
            settled.append(sid)
        if not settled:
            break
        pending = [sid for sid in pending if sid not in settled]

    top = [sid for sid in by_id if not parent_of[sid]]
    if len(top) < MIN_TOP_LEVEL_SCENES:
        return False   # 顶层会被掏空 → 整批放弃，保持现状

    changed = False
    for sid, s in by_id.items():
        want = parent_of[sid]
        if scene_parent(s) == want:
            continue
        m = dict(s.get("map")) if isinstance(s.get("map"), dict) else {}
        if want:
            m["parent"] = want
        else:
            m.pop("parent", None)
        # 换层就丢掉旧坐标，交给落位器在**新的那一层**重排。坐标是相对某个坐标空间才有
        # 意义的，顶层排出来的 (-1,6) 搬进子沙盘只是个无主的残值——留着，子沙盘就会原样
        # 继承顶层那份散乱（村庄遗址的四间屋子会散落在半径 4 格开外）。
        m.pop("q", None)
        m.pop("r", None)
        s["map"] = m
        changed = True
    return changed


def _axial_round(qf: float, rf: float) -> tuple[int, int]:
    """cube 取整（q+r+s=0）：把重心浮点坐标收敛到最近的合法 hex。"""
    sf = -qf - rf
    q, r, s = round(qf), round(rf), round(sf)
    dq, dr, ds = abs(q - qf), abs(r - rf), abs(s - sf)
    if dq > dr and dq > ds:
        q = -r - s
    elif dr > ds:
        r = -q - s
    return int(q), int(r)


def _spiral(center: tuple[int, int], max_radius: int = 128):
    """从 center 向外按环遍历（确定性顺序）。max_radius=128 覆盖 4 万+ 格，实际用不满。"""
    yield center
    cq, cr = center
    for k in range(1, max_radius + 1):
        q, r = cq + _DIRS[4][0] * k, cr + _DIRS[4][1] * k
        for d in range(6):
            for _ in range(k):
                yield (q, r)
                q += _DIRS[d][0]
                r += _DIRS[d][1]


def ensure_scene_maps(scenes: list) -> bool:
    """校验并修复全部 location 场景的 map（原地改 scene dict）。返回是否有改动。

    - 合法提议保留：整数 q/r 且未与先前场景撞格（列表序先到先得）；
    - 缺失/冲突者重新落位：已定位邻居最多的先放，落在邻居重心最近的空格；
      无已定位邻居则落全图重心附近；全图空白则第一个放原点；
    - biome 归一到枚举（小写），缺失/非法默认 plain；
    - chapter 场景不上图，误给的 map 清掉。
    """
    locs = [s for s in scenes or [] if isinstance(s, dict) and s.get("kind") != "chapter"]
    changed = False
    for s in scenes or []:
        if isinstance(s, dict) and s.get("kind") == "chapter" and "map" in s:
            s.pop("map", None)
            changed = True
    if not locs:
        return changed

    # 按父级分组，**每一层各跑一次落位**：顶层的 (0,0) 与「村庄遗址」子沙盘的 (0,0)
    # 是两个互不相干的格子。落位算法本身一字未改，只是作用域从「全图」缩到「一层」。
    groups: dict[str, list[dict]] = {}
    for s in locs:
        groups.setdefault(scene_parent(s), []).append(s)
    for parent, group in groups.items():
        changed = _layout_group(group, reserve_origin=bool(parent)) or changed
    return changed


def _layout_group(locs: list[dict], reserve_origin: bool = False) -> bool:
    """把同一层的场景落位到该层自己的坐标空间（原落位算法，作用域＝一层）。

    ``reserve_origin``（子沙盘专用）把原点空出来给父级地点本身。模组里的连通几乎总是
    星形——四间屋子各自只连「村庄遗址」，彼此不相连（闇暗山实测）。父级不在场，星形就
    没有中心，子沙盘里一条连线都画不出来，四个格子彼此悬空。留出原点，父级坐这个位置，
    连线自然成立，它同时也是「走出去」的锚点。
    """
    changed = False
    coord_of: dict[int, tuple[int, int]] = {}   # locs 下标 → 坐标
    if reserve_origin:
        # 已经占着原点的场景要挪走：那个位置属于父级
        for s in locs:
            if scene_coord(s) == (0, 0) and isinstance(s.get("map"), dict):
                m = dict(s["map"])
                m.pop("q", None)
                m.pop("r", None)
                s["map"] = m
                changed = True
    used: set[tuple[int, int]] = {(0, 0)} if reserve_origin else set()
    todo: list[int] = []
    for i, s in enumerate(locs):
        c = scene_coord(s)
        if c is not None and c not in used and all(axial_distance(c, other) >= 2 for other in used):
            coord_of[i] = c
            used.add(c)
        else:
            todo.append(i)

    idx_by_id = {str(s.get("id")): i for i, s in enumerate(locs) if s.get("id")}
    adj: dict[int, set[int]] = {i: set() for i in range(len(locs))}
    for i, s in enumerate(locs):
        for c in s.get("connections") or []:
            j = idx_by_id.get(str(c or "").strip())
            if j is not None and j != i:
                adj[i].add(j)
                adj[j].add(i)

    while todo:
        # 已定位邻居最多者优先（并列取列表序）——让链式结构沿着已放好的一端生长
        todo.sort(key=lambda i: (-sum(1 for j in adj[i] if j in coord_of), i))
        i = todo.pop(0)
        placed_nb = [coord_of[j] for j in adj[i] if j in coord_of]
        # 子沙盘里退回原点而不是「全层重心」：同层的场景彼此几乎不相连（都只连父级），
        # 一个已定位邻居都找不到，按重心找位就会把它们排成一条越走越远的链。
        # 父级坐在原点、人人都连它——围着原点铺开才是这层真实的拓扑。
        anchor = placed_nb or ([] if reserve_origin else list(coord_of.values()))
        if anchor:
            target = _axial_round(
                sum(c[0] for c in anchor) / len(anchor),
                sum(c[1] for c in anchor) / len(anchor),
            )
        else:
            target = (0, 0)
        spot = next(c for c in _spiral(target) if c not in used and all(axial_distance(c, other) >= 2 for other in used))
        coord_of[i] = spot
        used.add(spot)
        changed = True

    for i, s in enumerate(locs):
        q, r = coord_of[i]
        m = s.get("map") if isinstance(s.get("map"), dict) else {}
        biome = str(m.get("biome") or "").strip().lower()
        if biome not in BIOMES:
            biome = "plain"
        new_map = {"q": q, "r": r, "biome": biome}
        # parent 是归组的产物、不是落位的产物：重写整个 map 时必须原样带上，
        # 否则每跑一次修复器就把层级抹平一次（幂等性直接坏掉）。
        if parent := scene_parent(s):
            new_map["parent"] = parent
        if s.get("map") != new_map:
            s["map"] = new_map
            changed = True
    return changed


def ensure_module_map(db, module) -> bool:
    """存量模组懒回填：scenes 过归组与修复器，有改动才落库（幂等；JSON 列须整体重赋值）。

    归组必须排在落位之前：`ensure_scene_maps` 是按 parent 分层落位的，先定层再落格。
    """
    scenes = [dict(s) if isinstance(s, dict) else s for s in (module.scenes or [])]
    changed = infer_scene_parents(scenes)
    if not ensure_scene_maps(scenes) and not changed:
        return False
    module.scenes = scenes
    module.map_nodes = _synced_map_nodes(module, scenes)
    db.add(module)
    db.commit()
    return True


def _synced_map_nodes(module, scenes: list) -> list:
    """把 map_nodes 里「有 scene_id 的那些」的坐标与地貌对齐到 scenes（纯函数，返回新列表）。

    map_nodes 是坐标的第二份拷贝（模组详情页的沙盘直接读它），此前只有 set_scene_map
    这一条路在维护。归组会把子级场景重排到**子层坐标空间**，不同步就会出现：详情页按旧的
    顶层坐标把四间屋子摊在子沙盘的四个角上——数据是对的，看着却像没归组。
    地貌节点（无 scene_id）不动，它们本就只属于顶层。
    """
    by_id = {str(s.get("id")): s for s in scenes if isinstance(s, dict) and s.get("id")}
    out = []
    for node in (getattr(module, "map_nodes", None) or []):
        if not isinstance(node, dict):
            continue
        node = dict(node)
        scene = by_id.get(str(node.get("scene_id") or ""))
        coord = scene_coord(scene) if scene else None
        if coord is not None:
            node["q"], node["r"] = coord
            node["biome"] = (scene.get("map") or {}).get("biome") or node.get("biome") or "plain"
        out.append(node)
    return out


def set_scene_map(db, module, scene_id: str, q: int, r: int, biome: str | None = None) -> dict:
    """KP 拖拽落位：把指定场景移到 (q, r)，可顺带改地貌。

    校验后整体重赋值 scenes（JSON 列）并落库。非法情形抛 ValueError（调用方转 400）：
    场景不存在 / chapter 不上沙盘 / 目标格已被占 / 显式给了未知地貌。
    """
    scenes = [dict(s) if isinstance(s, dict) else s for s in (module.scenes or [])]
    target = next(
        (s for s in scenes if isinstance(s, dict) and s.get("id") == scene_id), None,
    )
    if target is None:
        raise ValueError("场景不存在")
    if target.get("kind") == "chapter":
        raise ValueError("章节场景不上沙盘")
    q, r = int(q), int(r)
    # 只和**同一层**的场景比占位：顶层的 (2,4) 与某个子沙盘里的 (2,4) 是两个格子，
    # 不按层过滤就会出现「拖到空地却报该格已被占用」这种查无实据的拒绝。
    layer = scene_parent(target)
    for s in scenes:
        if not isinstance(s, dict) or s.get("id") == scene_id or scene_parent(s) != layer:
            continue
        if scene_coord(s) == (q, r):
            raise ValueError(f"该格已被「{s.get('title') or s.get('id')}」占用")
        other = scene_coord(s)
        if other is not None and axial_distance((q, r), other) < 2:
            raise ValueError("场景节点之间至少需要间隔一个普通节点")
    old = target.get("map") if isinstance(target.get("map"), dict) else {}
    keep_parent = str(old.get("parent") or "").strip()
    if biome is not None:
        b = str(biome).strip().lower()
        if b not in BIOMES:
            raise ValueError(f"未知地貌：{biome}")
    else:
        b = str(old.get("biome") or "").strip().lower()
        if b not in BIOMES:
            b = "plain"
    target["map"] = {"q": q, "r": r, "biome": b}
    if keep_parent:   # 拖拽只改格子，不改它挂在谁下面
        target["map"]["parent"] = keep_parent
    # 兼容仍使用 PATCH /scene-map 的调用方：同步统一地图节点，避免下一次读取时旧节点覆盖新位置。
    map_nodes = [dict(node) for node in (getattr(module, "map_nodes", None) or []) if isinstance(node, dict)]
    updated_node = False
    for node in map_nodes:
        if str(node.get("scene_id") or "") == str(scene_id) or node.get("id") == scene_id:
            node.update({"id": scene_id, "q": q, "r": r, "biome": b, "scene_id": scene_id})
            updated_node = True
            break
    if not updated_node:
        map_nodes.append({"id": scene_id, "q": q, "r": r, "biome": b, "scene_id": scene_id})
    module.map_nodes = map_nodes
    module.scenes = scenes
    db.add(module)
    db.commit()
    return target["map"]


def neighbor_label(cur_scene: dict | None, nb_scene: dict | None) -> str | None:
    """「北・紧邻」式方位标签；任一侧无坐标返回 None（旧模组 fail-open，不阻塞）。"""
    a, b = scene_coord(cur_scene), scene_coord(nb_scene)
    if a is None or b is None or a == b:
        return None
    return f"{direction_word(a, b)}・{distance_word(axial_distance(a, b))}"


def biome_label(scene: dict | None) -> str | None:
    m = (scene or {}).get("map")
    if not isinstance(m, dict):
        return None
    return BIOME_LABELS.get(str(m.get("biome") or "").strip().lower())
