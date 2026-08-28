"""场景导航与地点可见性服务：位置、已知地点、连通图与沙盘节点。

自 ``session_service.py`` 拆出。这一簇只回答「谁在哪儿、哪些地方可见、怎么去」，
不碰席位与授权。``session_service`` 保留同名 re-export。
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.models.session import GameSession
from app.models.session_navigation import SessionNavigation
from app.services import world_memory
from app.services.event_store import KP_ONLY_SENTINEL, get_session_events


def update_scene(db: Session, session_id: str, scene_id: str) -> None:
    session = db.get(GameSession, session_id)
    if not session:
        return
    session.current_scene_id = scene_id
    nav = db.get(SessionNavigation, session_id)
    if nav is None:
        nav = SessionNavigation(session_id=session_id)
        db.add(nav)
    visited = list(nav.visited_scenes or [])
    if scene_id not in visited:
        visited.append(scene_id)
    nav.visited_scenes = visited
    db.commit()


# ── 按角色位置 / 已知地点（分头行动 + 大地图前往）──────────────────

def get_party_locations(session: GameSession) -> dict:
    """session_navigation.party_locations：{角色 id: 所在场景 id}。缺省时按需回落到当前场景。"""
    nav = session.navigation
    return dict((nav.party_locations if nav else {}) or {})


def get_visited_scenes(session: GameSession) -> list:
    """session_navigation.visited_scenes：队伍真正到访过的场景 id（只进不出）。"""
    nav = session.navigation
    return list(nav.visited_scenes if nav else [])


def get_char_location(session: GameSession, char_id: str | None) -> str | None:
    """某角色当前所在场景；无显式记录则回落到会话当前场景（向后兼容）。"""
    if not char_id:
        return session.current_scene_id
    return get_party_locations(session).get(char_id) or session.current_scene_id


def set_char_location(db: Session, session_id: str, char_id: str, scene_id: str) -> None:
    """把某角色移动到某场景（玩家经大地图前往 / AI 队友分头时的落点）。

    主角移动时一并更新 current_scene_id（地图面板、NPC 上下文等仍以它为锚）。目的地记入已访问。
    """
    if not (char_id and scene_id):
        return
    session = db.get(GameSession, session_id)
    if not session:
        return
    nav = db.get(SessionNavigation, session_id)
    if nav is None:
        nav = SessionNavigation(session_id=session_id)
        db.add(nav)
    locs = dict(nav.party_locations or {})
    locs[char_id] = scene_id
    nav.party_locations = locs
    visited = list(nav.visited_scenes or [])
    if scene_id not in visited:
        visited.append(scene_id)
    nav.visited_scenes = visited
    if char_id == session.player_character_id:
        session.current_scene_id = scene_id
    db.commit()


#: 「本轮明确留守」的事件标记。位置默认跟随队伍，只有显式表过态的人自理。
STAY_META_KEY = "stay"


def stayed_char_ids(db: Session, session_id: str) -> set[str]:
    """本回合明确说过「我留在这儿」的角色 id。

    队伍位置的默认语义是**跟随**：没有位置记录的人被 `get_char_location` 回落到当前场景，
    主角一走就跟着走。这对绝大多数回合是对的，但它让「你们仨去街区，我留下继续问诺特」
    这个意图在系统里无处安放——莫妮卡人被判定在街区，KP 却还在演她留在事务所。

    留守做成**本轮事件**而不是持久状态：它描述的是这一次移动跟不跟，不是一种长期属性；
    队友下一轮想跟上，正常行动即可，不必再撤销一个标志位。
    """
    from app.services.turn_context import _current_turn_events

    out: set[str] = set()
    for ev in _current_turn_events(get_session_events(db, session_id)):
        meta = ev.metadata_ or {}
        if meta.get(STAY_META_KEY) and ev.actor_id:
            out.add(str(ev.actor_id))
    return out


# 地点名常见的「设施类型」后缀：按长度从长到短，供从场景标题析出可被对话提及的关键词。
_FACILITY_SUFFIXES = [
    "疗养院", "图书馆", "档案馆", "博物馆", "派出所", "警察局", "礼拜堂", "老房子",
    "报社", "医院", "教堂", "法院", "老宅", "宅邸", "公寓", "旅馆", "酒店",
    "饭店", "学校", "大学", "中学", "小学", "墓地", "墓园", "工厂", "仓库", "教会",
    "庄园", "别墅", "城堡", "监狱", "银行", "邮局", "车站", "码头", "农场", "矿场",
    "洞穴", "地窖", "街区", "房子", "宅", "街",
]


# 「状态修饰」后缀：跟在设施类型之后表示地点当下状态（沉思礼拜堂+废墟）。它们**只用于剥离**
# 得到核心地名，本身**不**作为解锁关键词（否则说个「废墟」就乱解锁）。
_MODIFIER_SUFFIXES = ["废墟", "遗址", "旧址", "遗迹", "残址", "废址", "旧宅"]


def derive_scene_keywords(title: str) -> set[str]:
    """从场景标题**确定性地**派生解锁关键词：完整标题 + 核心地名（剥离废墟/遗址等状态词）+
    设施类型后缀 + 专名前缀。玩家在对话/行动里提到其中任意一个即解锁该地点。

    例：
      「罗克斯伯里疗养院」→ {完整标题, "疗养院", "罗克斯伯里"}
      「沉思礼拜堂废墟」  → {完整标题, "沉思礼拜堂", "礼拜堂", "沉思"}
        （此前只得完整标题，故必须说全名才解锁——本函数补上核心名与专名）

    这是**兜底/派生**逻辑：新模组解析时会另外生成并存储更丰富的 keywords（含地址/俗称），
    运行时二者取并集（见 known_scene_ids）。
    """
    title = (title or "").strip()
    if not title:
        return set()
    keywords = {title}
    # 先剥掉结尾的状态修饰后缀（可叠多个）得到核心地名，核心名本身入库
    core = title
    changed = True
    while changed:
        changed = False
        for suf in _MODIFIER_SUFFIXES:
            if core.endswith(suf) and len(core) > len(suf):
                core = core[: -len(suf)].strip("·的 ")
                changed = True
    if core != title and len(core) >= 2:
        keywords.add(core)
    # 对核心名跑设施后缀逻辑：加设施类型别名（礼拜堂）+ 最长后缀前的专名（沉思）
    matched = [suf for suf in _FACILITY_SUFFIXES if core.endswith(suf) and len(core) > len(suf)]
    keywords.update(matched)
    if matched:
        longest = max(matched, key=len)
        prefix = core[: -len(longest)].strip("·的 ")
        if len(prefix) >= 2:
            keywords.add(prefix)
    return {k for k in keywords if len(k) >= 2}


def scene_unlock_keywords(scene: dict) -> set[str]:
    """一个场景的全部解锁关键词 = 存储的 keywords（解析时生成，含地址/俗称）∪ 标题派生关键词。
    存储缺失（老模组）时退化为纯派生——这样『沉思礼拜堂废墟』说「沉思礼拜堂」也能解锁。"""
    stored = {
        k.strip() for k in (scene.get("keywords") or [])
        if isinstance(k, str) and len(k.strip()) >= 2
    }
    title = scene.get("title") or scene.get("name") or ""
    return stored | derive_scene_keywords(title)


_NUMBERED_CARRIAGE_RE = re.compile(r"(?<!\d)(\d+)\s*号车厢")
_NEXT_CARRIAGE_RE = re.compile(r"下一(?:节|个)?车厢")


def _carriage_number(scene: dict | None) -> int | None:
    """从场景名或解锁词中读取阿拉伯数字车厢号。"""
    if not scene:
        return None
    labels = [scene.get("title"), scene.get("name"), *(scene.get("keywords") or [])]
    for label in labels:
        if not isinstance(label, str):
            continue
        matched = _NUMBERED_CARRIAGE_RE.search(label)
        if matched:
            return int(matched.group(1))
    return None


def _relative_carriage_mentions(by_id: dict, event) -> set[str]:
    """解析「下一节车厢」这种依赖叙事所在场景的相对称呼。

    只在事件带 ``metadata.scene_id``、且目标确实与来源场景直连时解析，避免把旧叙事按
    会话当前场景重新解释，也避免顺着编号提前揭露不可达地点。
    """
    content = getattr(event, "content", "") or ""
    if not _NEXT_CARRIAGE_RE.search(content):
        return set()
    metadata = getattr(event, "metadata_", None) or {}
    source_id = metadata.get("scene_id")
    source = by_id.get(source_id)
    source_number = _carriage_number(source)
    if source_number is None:
        return set()
    connected = {str(sid) for sid in (source.get("connections") or []) if sid}
    connected.update(
        sid for sid, scene in by_id.items()
        if source_id in {str(item) for item in (scene.get("connections") or []) if item}
    )
    return {
        sid for sid in connected
        if _carriage_number(by_id.get(sid)) == source_number + 1
    }


def known_scene_ids(module, session: GameSession, events: list | None = None) -> set:
    """已知地点 = 已访问/当前所在 ∪ 玩家可见叙事中被提及过的场景。

    未访问、且玩家可见内容从未提及的地点不在大地图上显示。KP-only 幕后事件绝不能
    参与解锁；带场景元数据的「下一节车厢」等确定性相对称呼也会解析为真实场景。
    """
    by_id = {s.get("id"): s for s in (module.scenes or []) if s.get("id")}
    known = set(get_visited_scenes(session))
    if session.current_scene_id:
        known.add(session.current_scene_id)
    # KP 挂过「要前往【X】吗」的地点必然已知——这是确定性记录，不该绕道去卡片文案里
    # 匹配场景名（卡片措辞一改，或场景名与关键词对不上，地点就会莫名其妙地消失）。
    known.update((session.world_state or {}).get("travel_suggested") or [])
    visible_events = [
        event for event in (events or [])
        if getattr(event, "event_type", None) in ("narration", "dialogue", "action", "system")
        and KP_ONLY_SENTINEL not in (getattr(event, "visibility", None) or [])
    ]
    convo = "\n".join(
        (getattr(event, "content", "") or "") for event in visible_events
    )
    if convo:
        for sid, s in by_id.items():
            if sid in known:
                continue
            if any(kw in convo for kw in scene_unlock_keywords(s)):
                known.add(sid)
    for event in visible_events:
        known.update(_relative_carriage_mentions(by_id, event))
    return {sid for sid in known if sid in by_id}


def _scene_adjacency(module) -> dict[str, set[str]]:
    """场景连通图（``connections`` 的无向闭包）：作者单向填写也按双向通行。"""
    adj: dict[str, set[str]] = {}
    ids = {s.get("id") for s in (module.scenes or []) if s.get("id")}
    for s in (module.scenes or []):
        sid = s.get("id")
        if not sid:
            continue
        adj.setdefault(sid, set())
        for c in s.get("connections") or []:
            c = str(c or "").strip()
            if c and c != sid and c in ids:
                adj.setdefault(c, set())
                adj[sid].add(c)
                adj[c].add(sid)
    return adj


def scene_neighbors(module, scene_id: str | None) -> list[str]:
    """当前场景可直达的相邻场景 id（升序）。无图/无该场景返回空列表。"""
    if not scene_id:
        return []
    return sorted(_scene_adjacency(module).get(scene_id, ()))


def find_scene_path(
    module, start: str | None, dest: str, via_allowed: set[str] | None = None,
) -> list[str] | None:
    """沿场景连通图找 ``start → dest`` 的最短路径（BFS，路径含两端点）。

    返回场景 id 列表；**确实不连通**返回 None（调用方据此拒绝切换）。
    保守把关——以下情形一律视为可直达（返回平凡路径，行为与没有连通图时一致）：
    - 模组任何场景都没填 connections（旧/手工模组，没建图，不能把它们走死）；
    - start 缺失（无当前位置可依据）；
    - start 或 dest 自身没有任何边（作者没为该地点建边，无拓扑可循）。

    ``via_allowed`` 给定时，**途经**的场景必须落在这个集合里（终点不受此限——终点正是要
    去的新地方）。玩家发起的移动一律带上它并传「已到访场景」：光看图上连通会放行「取道
    一个从没去过的车厢抵达先头车厢」这种路线，而抵达叙述又明确「途经不停留、不触发事件」，
    合起来就是**无视沿途的怪物与门锁凭空穿过去**。想去更远的地方，就得一段一段真的走过去。
    """
    dest = (dest or "").strip()
    if not dest:
        return None
    if not start:
        return [dest]
    if start == dest:
        return [start]
    adj = _scene_adjacency(module)
    if not any(adj.values()):
        return [start, dest]
    if not adj.get(start) or not adj.get(dest):
        return [start, dest]
    seen = {start}
    queue: list[list[str]] = [[start]]
    while queue:
        path = queue.pop(0)
        for nxt in sorted(adj.get(path[-1], ())):
            if nxt in seen:
                continue
            if nxt == dest:
                return path + [nxt]
            seen.add(nxt)
            # 只有允许途经的场景才继续往下扩；起点永远算数（人就站在那儿）。
            if via_allowed is not None and nxt not in via_allowed:
                continue
            queue.append(path + [nxt])
    return None


def travel_blocker(
    module, session: GameSession, start: str | None, dest: str,
) -> tuple[str, str] | None:
    """玩家为什么去不了 ``dest``：返回 (挡路的场景 id, 原因)；能去则 None。

    只在 find_scene_path(..., via_allowed=可通行) 判定去不了时才有意义——用图上连通的
    完整路径反查第一个过不去的途经点，好把「不连通」这种没头没脑的报错换成
    「要去先头车厢，得先过 2 号车厢」。真的完全不连通时返回 None（那是另一种拒绝）。
    """
    full = find_scene_path(module, start, dest)
    if not full:
        return None
    visited = visited_scene_ids(session)
    blocked = world_memory.blocked_scenes(session.world_state or {})
    for sid in full[1:-1]:
        if sid in blocked:
            return sid, blocked[sid] or "那边过不去"
        if sid not in visited:
            return sid, "那儿你们还没去过"
    return None


def passable_scene_ids(session: GameSession) -> set[str]:
    """可作为**途经点**的场景：去过的，减去当前被 KP 判定过不去的。

    终点不受此限——走进一个危险或封锁的地方是玩家的自由，被拦住的是「借道穿过去」。
    """
    return visited_scene_ids(session) - set(
        world_memory.blocked_scenes(session.world_state or {})
    )


def visited_scene_ids(session: GameSession) -> set[str]:
    """队伍真正到过的场景（含当前所在）——「能否途经」的唯一判据。

    与 known_scene_ids 的区别是本文件里最容易混的一处：``known`` 含「叙述里被提到过」的
    地点（它们要在大地图上看得见、可作为**终点**），``visited`` 只含真正去过的
    （只有这些能当**途经点**）。玩家听说过驾驶室，不等于知道怎么绕过中间那节车厢。
    """
    out = set(get_visited_scenes(session))
    if session.current_scene_id:
        out.add(session.current_scene_id)
    for sid in get_party_locations(session).values():
        if sid:
            out.add(str(sid))
    return out


def list_known_locations(
    module, session: GameSession, char_id: str | None = None, events: list | None = None,
    char_names: dict[str, str] | None = None, reveal_all: bool = False,
) -> list[dict]:
    """供「大地图/调查板/沙盘」渲染：已知地点列表（当前所在、已访问、相互连接、队友分布）。

    - ``kind == "chapter"`` 的场景是叙事章节而非地点，不上图（当前正身处其中时除外）。
    - ``connections`` 只回展示集合内的邻居——玩家侧未知地点绝不经边泄露。
    - ``char_names``（char_id → 名字）给定时，按 party_locations 归并各地点的在场成员。
    - ``reveal_all=True``（真人 KP 上帝视角）：返回全部 location 场景并附 ``known`` 标记，
      前端「玩家视角」开关据此纯客户端过滤；玩家侧永远走迷雾路径（known 恒 True）。
    """
    by_id = {s.get("id"): s for s in (module.scenes or []) if s.get("id")}
    visited = set(get_visited_scenes(session))
    cur = get_char_location(session, char_id)
    # 层级门禁：挂在某个父级地点之下的场景，父级被**真正到过**之前一律不可见。
    # 这既是防剧透（开局就不该知道村里有几间屋子），也让子沙盘有个明确的解锁时刻。
    # 判据用 visited 而不是 known：听说过村庄不等于进过村、更不等于看得见村里的门牌。
    # 队伍当前所在的场景永不隐藏（分头行动时有人已经在里面了）。
    from app.services import hex_map

    # KP 明确挂过「要前往【X】吗」的地点解除门禁：它已经被点名了，藏着只剩坏处——
    # 玩家收到邀请却在地图上找不到入口，只会以为系统坏了。门禁防的是「还没听说过的
    # 地方提前曝光」，而这个地方 KP 自己说破了，剧透早已发生。
    suggested = set((session.world_state or {}).get("travel_suggested") or [])

    def _unlocked(sid: str) -> bool:
        parent = hex_map.scene_parent(by_id.get(sid))
        return not parent or parent in visited or sid == cur or sid in suggested

    known = {
        sid for sid in known_scene_ids(module, session, events)
        if (by_id[sid].get("kind") != "chapter" or sid == cur) and _unlocked(sid)
    }
    if reveal_all:
        # KP 上帝视角看得见全部（含未解锁的子级），由 known 标记如实告诉他玩家看不看得见
        shown = {sid for sid, s in by_id.items() if s.get("kind") != "chapter" or sid == cur}
    else:
        shown = known
    # 队伍分布：各成员所在场景（party_locations 缺省回落主场景）
    party_at: dict[str, list[str]] = {}
    if char_names:
        pl = get_party_locations(session)
        for cid, name in char_names.items():
            sid = pl.get(cid) or session.current_scene_id
            if sid:
                party_at.setdefault(sid, []).append(name)
    # 调查板红线：**已发现**的线索（clue_ledger）按其模组定义的 location 挂到地点上。
    # 只含玩家已触碰的线索——未发现的绝不上板（不剧透）。
    ledger = (session.world_state or {}).get("clue_ledger") or {}
    clue_by_id = {c.get("id"): c for c in (getattr(module, "clues", None) or []) if c.get("id")}
    clues_at: dict[str, list[dict]] = {}
    for cid, entry in ledger.items():
        cdef = clue_by_id.get(cid)
        loc = (cdef or {}).get("location")
        if cdef and loc:
            clues_at.setdefault(loc, []).append({
                "id": cid,
                "name": cdef.get("name") or cid,
                "status": (entry or {}).get("status") or "partial",
            })
    out = []
    for sid in shown:
        s = by_id[sid]
        conns = [c for c in (s.get("connections") or []) if c in shown and c != sid]
        out.append({
            "id": sid,
            "name": s.get("title") or s.get("name") or sid,
            "current": sid == cur,
            "visited": sid in visited,
            "connections": conns,
            "party": party_at.get(sid, []),
            "clues": clues_at.get(sid, []),
            "map": s.get("map"),   # 沙盘坐标与地貌（旧模组未回填时为 None）
            "known": sid in known,  # KP 上帝视角下标记玩家是否已知；玩家侧恒 True
            # 场景配图：前端拿它做「场景氛围底」的色调来源。之所以从这里给而不是只靠聊天流里
            # 那条「抵达」插图消息——那条消息可能压根不在已加载的分页里（存量存档翻页只取最近
            # 一段），而配图生成后是回写进 scene.image 的，这份数据一直都在。
            # 本函数的 cur 取的是**查看者自己**的角色位置，分头行动时各人也就各看各的场景。
            "image": s.get("image") or "",
        })
    out.sort(key=lambda x: (not x["current"], not x["visited"], x["id"]))
    return out


def list_visible_map_nodes(module, locations: list[dict], reveal_all: bool = False) -> list[dict]:
    """返回沙盘需要绘制的统一节点；普通节点只作为地貌，不参与旅行。"""
    nodes = list(getattr(module, "map_nodes", None) or [])
    shown_ids = {str(item.get("id")) for item in locations if item.get("id")}
    out = []
    for node in nodes:
        sid = str(node.get("scene_id") or "")
        if sid:
            if sid in shown_ids:
                out.append(node)
            elif not reveal_all:
                # 未发现的场景保留其地貌格，但清掉 scene_id，让前端不要绘制剧情 token。
                hidden = dict(node)
                hidden["scene_id"] = None
                out.append(hidden)
            continue
        if reveal_all:
            out.append(node)
            continue
        # 普通地貌是地图底图的一部分，完整下发，未知场景不会造成视觉上的“挖空”。
        out.append(node)
    return out
