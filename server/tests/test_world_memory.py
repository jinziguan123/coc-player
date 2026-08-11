"""世界记忆层 v1（线索台账 + NPC 记忆）的单测：纯函数、确定性钩子与上下文注入。

不调 LLM：纯函数直接断言；带库的钩子用临时 SQLite（沿用 test_chat_service 的桩法）。
"""

import asyncio
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.ai import story_summarizer, turn_planner
from app.ai.context import build_kp_context, build_npc_context
from app.models.base import Base
from app.models.character import Character
from app.models.event_log import EventLog
from app.models.module import Module
from app.models.session import GameSession
from app.models.session_participant import SessionParticipant  # noqa: F401 — 注册建表
from app.services import chat_service, planned_effects, world_memory


# ── 纯函数：台账写入 ──────────────────────────────────────────────


def test_record_clue_reveal_levels_and_merge():
    ws = world_memory.record_clue_reveal(
        {}, ["clue_key"], "hint", ["char_a"], 10, note="在书桌暗格附近有所察觉",
    )
    entry = ws["clue_ledger"]["clue_key"]
    assert entry["status"] == "partial"          # hint → partial
    assert entry["discovered_by"] == ["char_a"]
    assert entry["seq"] == 10

    # direct → known 升级；discovered_by 增量合并去重；seq 保留首次触碰值
    ws = world_memory.record_clue_reveal(ws, ["clue_key"], "direct", ["char_a", "char_b"], 20)
    entry = ws["clue_ledger"]["clue_key"]
    assert entry["status"] == "known"
    assert entry["discovered_by"] == ["char_a", "char_b"]
    assert entry["seq"] == 10

    # known 不降级：后续 hint 不会退回 partial
    ws = world_memory.record_clue_reveal(ws, ["clue_key"], "hint", ["char_c"], 30)
    assert ws["clue_ledger"]["clue_key"]["status"] == "known"


def test_record_clue_reveal_none_is_noop():
    ws = {"flags": {"x": True}}
    out = world_memory.record_clue_reveal(ws, ["clue_key"], "none", ["char_a"], 1)
    assert "clue_ledger" not in out
    out = world_memory.record_clue_reveal(ws, [], "direct", ["char_a"], 1)
    assert "clue_ledger" not in out


def test_record_clue_reveal_does_not_mutate_input():
    ws = {"clue_ledger": {"old": {"status": "partial"}}}
    world_memory.record_clue_reveal(ws, ["new"], "direct", ["char_a"], 5)
    assert "new" not in ws["clue_ledger"]  # 入参不被就地修改（读-改-写返回新 dict）


def test_discovered_clue_status():
    ws = {"clue_ledger": {
        "a": {"status": "known"}, "b": {"status": "partial"}, "c": {"status": "weird"},
    }}
    assert world_memory.discovered_clue_status(ws) == {"a": "known", "b": "partial"}
    assert world_memory.discovered_clue_status({}) == {}


# ── 纯函数：场景机制点台账 ───────────────────────────────────────


def test_record_scene_event_seen_is_idempotent_and_pure():
    ws = {"flags": {"x": True}}
    out = world_memory.record_scene_event_seen(ws, "scene_8", 3, 317, note="进入最里面的小屋被拖拽")
    assert world_memory.scene_event_seen(out, "scene_8", 3)
    assert not world_memory.scene_event_seen(out, "scene_8", 4)
    assert "scene_events_seen" not in ws            # 入参不被就地修改
    assert out["flags"] == {"x": True}              # 其余键原样带过

    # 重复记只保留首次的 seq（同一桥段不会因为后来又被提到而改写发生时点）
    again = world_memory.record_scene_event_seen(out, "scene_8", 3, 999)
    assert again["scene_events_seen"]["scene_8:3"]["seq"] == 317


def test_scene_event_seen_on_empty_state():
    assert not world_memory.scene_event_seen({}, "scene_8", 0)
    assert not world_memory.scene_event_seen(None, "scene_8", 0)


def test_format_scene_events_section_marks_progress():
    scene = {"id": "scene_8", "events": [
        {"trigger": "阅读村规", "kind": "san_check"},
        {"trigger": "进入最里面的小屋被拖拽", "kind": "dice_check", "skill": "STR"},
    ]}
    ws = world_memory.record_scene_event_seen({}, "scene_8", 1, 317)
    text = world_memory.format_scene_events_section(ws, scene)
    assert "- 阅读村规：尚未发生" in text
    assert "- 进入最里面的小屋被拖拽：已发生" in text
    assert "绝不要重演" in text


def test_format_scene_events_section_empty_scene():
    assert world_memory.format_scene_events_section({}, None) == ""
    assert world_memory.format_scene_events_section({}, {"id": "s1"}) == ""


# ── 纯函数：NPC 互动环形缓冲 ─────────────────────────────────────


def test_npc_interactions_ring_buffer_cap():
    ws = {}
    for i in range(12):
        ws = world_memory.record_npc_interaction(ws, "npc_butler", i, f"互动{i}")
    interactions = ws["npc_memory"]["npc_butler"]["interactions"]
    assert len(interactions) == world_memory.MAX_NPC_INTERACTIONS  # 上限 8
    assert interactions[0]["summary"] == "互动4"   # 最老的被挤出
    assert interactions[-1]["summary"] == "互动11"


def test_npc_interaction_preserves_other_fields():
    ws = {"npc_memory": {"npc_butler": {
        "attitude": "warming", "promises": ["答应半夜带玩家看西厢房"],
    }}}
    ws = world_memory.record_npc_interaction(ws, "npc_butler", 5, "被看穿慌张")
    entry = ws["npc_memory"]["npc_butler"]
    assert entry["attitude"] == "warming"          # 追加互动不冲掉既有字段
    assert entry["promises"] == ["答应半夜带玩家看西厢房"]
    assert entry["interactions"][-1]["summary"] == "被看穿慌张"


# ── 上下文注入 ───────────────────────────────────────────────


def _mem_module() -> Module:
    return Module(
        title="记忆测试模组", rule_system="coc",
        scenes=[{"id": "scene_hall", "name": "大厅"}],
        npcs=[{"id": "npc_butler", "name": "老管家", "initial_location": "scene_hall"}],
        clues=[{"id": "clue_key", "name": "书房钥匙", "location": "scene_hall"}],
    )


def _mem_session(world_state: dict) -> GameSession:
    s = GameSession(
        module_id="m1", player_character_id="char_a",
        current_scene_id="scene_hall", status="active", world_state=world_state,
    )
    return s


def _mem_char() -> Character:
    c = Character(name="亨利", rule_system="coc")
    c.id = "char_a"
    return c


def _one_event() -> list[EventLog]:
    return [EventLog(
        session_id="s1", sequence_num=1, event_type="action",
        actor_id="char_a", actor_name="亨利", content="我检查书桌",
    )]


def test_kp_context_contains_clue_ledger_and_npc_memory():
    ws = {
        "visited_scenes": ["scene_hall"],
        "clue_ledger": {"clue_key": {
            "status": "known", "discovered_by": ["char_a"], "seq": 3, "note": "暗格里找到",
        }},
        "npc_memory": {"npc_butler": {
            "attitude": "warming",
            "promises": ["答应半夜带玩家看西厢房"],
            "lies_told": ["谎称老爷死时自己在厨房"],
            "interactions": [{"seq": 2, "summary": "被亨利用心理学看穿慌张"}],
        }},
    }
    messages = build_kp_context(_mem_session(ws), _mem_module(), _mem_char(), _one_event())
    system = messages[0]["content"]
    assert "线索台账" in system
    assert "书房钥匙" in system and "完全掌握" in system
    assert "亨利" in system                       # discovered_by 的 id 已映射成角色名
    assert "绝不要再安排一次「发现」桥段" in system
    assert "NPC 记忆" in system
    assert "答应半夜带玩家看西厢房" in system
    assert "谎称老爷死时自己在厨房" in system


def test_kp_context_empty_ledger_still_lists_visible_clues():
    """台账一条没有时也要注入——静默才是病根。

    『闇暗山』那局跑了 6 个场景、台账全程为空（规划器一次都没填 clue_id），
    于是「不要重复安排发现桥段」这句硬指示从头到尾没进过上下文，KP 对着线索明文重演。
    """
    ws = {"visited_scenes": ["scene_hall"]}
    messages = build_kp_context(_mem_session(ws), _mem_module(), _mem_char(), _one_event())
    system = messages[0]["content"]
    assert "线索台账" in system
    assert "书房钥匙" in system and "尚未给出" in system
    assert "NPC 记忆" not in system   # NPC 记忆仍是「有才注入」，行为不变


def test_kp_context_opening_never_injects_ledger():
    ws = {"clue_ledger": {"clue_key": {"status": "known"}}}
    messages = build_kp_context(_mem_session(ws), _mem_module(), _mem_char(), [])
    assert "线索台账" not in messages[0]["content"]  # 开场隔离照旧


def test_kp_context_ledger_never_lists_unreached_clues():
    """全量对账不扩大泄露面：只列「线索」小节已经给过 KP 的那批。"""
    module = _mem_module()
    module.clues = [
        {"id": "clue_key", "name": "书房钥匙", "location": "scene_hall"},
        {"id": "clue_far", "name": "地窖账本", "location": "scene_cellar"},
    ]
    ws = {"visited_scenes": ["scene_hall"]}
    system = build_kp_context(_mem_session(ws), module, _mem_char(), _one_event())[0]["content"]
    assert "书房钥匙" in system
    assert "地窖账本" not in system      # 玩家还没去过地窖


def test_kp_context_injects_scene_events_progress():
    """当前场景的机制点逐条标已发生/未发生——KP 才不会把一次性桥段重演一遍。"""
    module = _mem_module()
    module.scenes = [{"id": "scene_hall", "name": "大厅", "events": [
        {"trigger": "阅读村规", "kind": "san_check"},
        {"trigger": "进入最里面的小屋被拖拽", "kind": "dice_check", "skill": "STR"},
    ]}]
    ws = {
        "visited_scenes": ["scene_hall"],
        "scene_events_seen": {"scene_hall:1": {"seq": 317, "note": ""}},
    }
    system = build_kp_context(_mem_session(ws), module, _mem_char(), _one_event())[0]["content"]
    assert "本场景机制点进度" in system
    assert "- 阅读村规：尚未发生" in system
    assert "- 进入最里面的小屋被拖拽：已发生" in system


def test_npc_context_injects_own_memory():
    ws = {"npc_memory": {"npc_butler": {
        "attitude": "wary",
        "promises": ["答应半夜带玩家看西厢房"],
        "lies_told": ["谎称老爷死时自己在厨房"],
        "interactions": [{"seq": 2, "summary": "被亨利用心理学看穿慌张"}],
    }}}
    messages = build_npc_context("npc_butler", _mem_session(ws), _mem_module(), [])
    system = messages[0]["content"]
    assert "你的记忆" in system
    assert "答应半夜带玩家看西厢房" in system
    assert "谎称老爷死时自己在厨房" in system
    assert "被亨利用心理学看穿慌张" in system


def test_npc_context_without_memory_unchanged():
    messages = build_npc_context("npc_butler", _mem_session({}), _mem_module(), [])
    assert "你的记忆" not in messages[0]["content"]


def test_turn_planner_marks_ledger_clues_discovered():
    ws = {
        "visited_scenes": ["scene_hall"],
        "clue_ledger": {"clue_key": {"status": "known", "discovered_by": ["char_a"]}},
    }
    messages = turn_planner.build_turn_plan_messages(
        _mem_session(ws), _mem_module(), _mem_char(), _one_event(),
    )
    user = messages[1]["content"]
    assert "不得再进入 candidate_clue_ids" in user
    payload = json.loads(user[user.index("{"):])
    assert payload["clue_ledger"] == {"clue_key": "known"}
    clue = next(c for c in payload["visible_clues"] if c["id"] == "clue_key")
    assert clue["discovered"] is True             # 台账 known → 已发现，不再是 candidate


def test_turn_planner_empty_ledger_backward_compatible():
    messages = turn_planner.build_turn_plan_messages(
        _mem_session({"visited_scenes": ["scene_hall"]}), _mem_module(), _mem_char(),
        _one_event(),
    )
    payload = json.loads(messages[1]["content"][messages[1]["content"].index("{"):])
    assert payload["clue_ledger"] == {}
    clue = next(c for c in payload["visible_clues"] if c["id"] == "clue_key")
    assert clue["discovered"] is False


def test_story_summary_prompt_mentions_ledger():
    messages = story_summarizer.build_summary_messages(
        "", [EventLog(session_id="s", sequence_num=1, event_type="narration", content="x")],
    )
    assert "台账" in messages[1]["content"]
    assert "剧情脉络" in messages[1]["content"]


# ── chat_service 确定性钩子（带库） ─────────────────────────────


@pytest.fixture
def db_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed(db):
    module = Module(
        title="记忆测试模组", rule_system="coc",
        npcs=[{"id": "npc_butler", "name": "老管家"}],
        clues=[{"id": "clue_key", "name": "书房钥匙"}],
    )
    player = Character(name="亨利", rule_system="coc")
    mate = Character(name="约翰", rule_system="coc")
    db.add_all([module, player, mate])
    db.flush()
    session = GameSession(
        module_id=module.id, player_character_id=player.id, status="active",
        world_state={},
    )
    db.add(session)
    db.commit()
    return session, module, player, mate


def test_record_clue_ledger_from_plan_hook(db_factory):
    db = db_factory()
    session, module, player, mate = _seed(db)
    plan = turn_planner.TurnPlan(
        clue_policy=turn_planner.CluePolicy(
            action_matches_clue=True,
            candidate_clue_ids=["clue_key"],
            reveal_level="direct",
            notes="书桌暗格已被打开",
        ),
    )
    events = [EventLog(
        session_id=session.id, sequence_num=7, event_type="action",
        actor_id=player.id, actor_name=player.name, content="我撬开暗格",
    )]
    chat_service._record_clue_ledger_from_plan(
        db, session, plan, events, player, [mate],
    )
    entry = (session.world_state or {}).get("clue_ledger", {}).get("clue_key")
    assert entry is not None
    assert entry["status"] == "known"             # direct → known
    assert player.id in entry["discovered_by"]    # 同场景队友一并在场
    assert mate.id in entry["discovered_by"]
    assert entry["seq"] == 7
    assert "书桌暗格" in entry["note"]


def test_record_clue_ledger_from_plan_none_level_noop(db_factory):
    db = db_factory()
    session, module, player, mate = _seed(db)
    plan = turn_planner.TurnPlan(
        clue_policy=turn_planner.CluePolicy(
            candidate_clue_ids=["clue_key"], reveal_level="none",
        ),
    )
    chat_service._record_clue_ledger_from_plan(db, session, plan, [], player, [mate])
    assert not (session.world_state or {}).get("clue_ledger")


def _search_plan(matches: bool = True):
    """典型「搜查+侦查检定」轮的裁定：匹配上了线索，但成败未定 → 本轮不可揭示。"""
    return turn_planner.TurnPlan(
        requires_check=True,
        clue_policy=turn_planner.CluePolicy(
            action_matches_clue=matches,
            candidate_clue_ids=["clue_key"],
            reveal_level="none",
            notes="检定成功才揭示暗格",
        ),
    )


def test_gated_clue_is_staged_then_settled_on_success(db_factory):
    """要掷骰才能拿到的线索：挂检定那轮先暂存，骰子成功后才入台账。

    这是台账此前恒空的根因——规划器在挂检定的那轮只能写 reveal_level=none
    （写别的就是提前泄底），而检定落地后再没有人记账。
    """
    from app.services import planned_effects

    db = db_factory()
    session, module, player, mate = _seed(db)
    events = [EventLog(
        session_id=session.id, sequence_num=7, event_type="action",
        actor_id=player.id, actor_name=player.name, content="我敲击书桌侧板找暗格",
    )]
    chat_service._record_clue_ledger_from_plan(db, session, _search_plan(), events, player, [mate])
    # 本轮不入台账（还没掷骰），但候选已暂存
    assert not (session.world_state or {}).get("clue_ledger")
    staged = (session.world_state or {}).get("pending_clue_reveals")
    assert staged and staged["ids"] == ["clue_key"] and staged["seq"] == 7

    planned_effects.settle_pending_clue_reveals(db, session.id, session, succeeded=True)
    entry = (session.world_state or {}).get("clue_ledger", {}).get("clue_key")
    assert entry and entry["status"] == "known"
    assert player.id in entry["discovered_by"] and mate.id in entry["discovered_by"]
    assert entry["seq"] == 7
    assert not (session.world_state or {}).get("pending_clue_reveals")   # 兑现即清空


def test_gated_clue_discarded_on_failed_check(db_factory):
    from app.services import planned_effects

    db = db_factory()
    session, module, player, mate = _seed(db)
    chat_service._record_clue_ledger_from_plan(db, session, _search_plan(), [], player, [mate])
    planned_effects.settle_pending_clue_reveals(db, session.id, session, succeeded=False)
    assert not (session.world_state or {}).get("clue_ledger")
    assert not (session.world_state or {}).get("pending_clue_reveals")


def test_unmatched_candidates_are_not_staged(db_factory):
    """只是列在候选里、行动并没匹配上 → 不暂存，别让下一次投骰白捡一条线索。"""
    db = db_factory()
    session, module, player, mate = _seed(db)
    chat_service._record_clue_ledger_from_plan(
        db, session, _search_plan(matches=False), [], player, [mate],
    )
    assert not (session.world_state or {}).get("pending_clue_reveals")


def test_direct_reveal_clears_stale_stage(db_factory):
    """当场揭示的那轮要清掉旧暂存——否则下一次投骰会把上一轮没掷的候选一并兑现。"""
    db = db_factory()
    session, module, player, mate = _seed(db)
    chat_service._record_clue_ledger_from_plan(db, session, _search_plan(), [], player, [mate])
    assert (session.world_state or {}).get("pending_clue_reveals")
    direct = turn_planner.TurnPlan(
        clue_policy=turn_planner.CluePolicy(
            action_matches_clue=True, candidate_clue_ids=["clue_other"], reveal_level="direct",
        ),
    )
    chat_service._record_clue_ledger_from_plan(db, session, direct, [], player, [mate])
    assert (session.world_state or {}).get("clue_ledger", {}).get("clue_other", {}).get("status") == "known"
    assert not (session.world_state or {}).get("pending_clue_reveals")


def test_record_npc_say_memory_hook(db_factory):
    db = db_factory()
    session, module, player, mate = _seed(db)
    extracted = [
        ("老管家", "老爷死时我在厨房，什么都没看见。"),
        ("老管家", "你们还是快些离开吧。"),      # 同一 NPC 一轮只记一条
        ("约翰", "我不信。"),                     # 队友台词不入 NPC 记忆
    ]
    chat_service._record_npc_say_memory(
        db, session.id, session, module, extracted, [player.name, mate.name],
    )
    memory = (session.world_state or {}).get("npc_memory", {})
    assert list(memory.keys()) == ["npc_butler"]
    interactions = memory["npc_butler"]["interactions"]
    assert len(interactions) == 1
    assert "对亨利、约翰说" in interactions[0]["summary"]
    assert "厨房" in interactions[0]["summary"]


def test_match_single_npc_requires_unique_hit():
    module = Module(title="t", rule_system="coc", npcs=[
        {"id": "npc_a", "name": "老管家"},
        {"id": "npc_b", "name": "女仆安娜"},
    ])
    assert chat_service._match_single_npc(module, "我用心理学观察老管家的神色") == (
        "npc_a", "老管家",
    )
    # 多命中 / 零命中：归属不成立，跳过
    assert chat_service._match_single_npc(module, "观察老管家和女仆安娜") is None
    assert chat_service._match_single_npc(module, "观察周围环境") is None
    assert chat_service._match_single_npc(module, "") is None


def test_apply_world_memory_fail_open(db_factory):
    db = db_factory()
    session, *_ = _seed(db)

    def _boom(ws):
        raise RuntimeError("boom")

    # 更新函数抛异常不得上抛（fail-open），world_state 保持原样
    chat_service._apply_world_memory(db, session, _boom)
    assert (session.world_state or {}) == {}


# ── 叙事进度记账：规划器漏记时的确定性兜底（带库）──────────────────


_VILLAGE = {
    "id": "scene_8", "title": "村庄遗址", "keywords": ["村庄"],
    "events": [
        {"trigger": "进入最里面的小屋被拖拽", "kind": "dice_check", "skill": "STR"},
        {"trigger": "阅读村规", "kind": "san_check", "san_loss": "0/1"},
    ],
}


def _seed_village(db):
    """『闇暗山』村庄遗址：石板与村规都在本场景，绘本在没去过的树洞。"""
    module = Module(
        title="闇暗山", rule_system="coc", npcs=[], scenes=[_VILLAGE],
        clues=[
            {"id": "clue_3", "name": "石板", "location": "scene_8"},
            {"id": "clue_5", "name": "村规", "location": "scene_8"},
            {"id": "clue_4", "name": "绘本《摘瘤爷爷》", "location": "scene_9"},
        ],
    )
    player = Character(name="沃什", rule_system="coc")
    db.add_all([module, player]); db.flush()
    session = GameSession(
        module_id=module.id, player_character_id=player.id, status="active",
        current_scene_id="scene_8", world_state={},
    )
    db.add(session); db.commit()
    return session, module, player


def _narrate(db, session, text, seq=10):
    db.add(EventLog(
        session_id=session.id, sequence_num=seq, event_type="narration",
        actor_name="KP", content=text, visibility=[], metadata={},
    ))
    db.commit()


def test_narrated_progress_records_clue_seen_in_narration(db_factory):
    """KP 把石板摆到玩家面前却没让规划器记账 → 兜底补挂（只记 partial）。

    取自实测那局的原句：规划器全程没填过 clue_id，台账一条没有。
    """
    db = db_factory()
    session, module, player = _seed_village(db)
    _narrate(db, session, "手电光打在石板表面，你蹲下身，指腹贴着板面一寸寸扫过去。刻痕摸得出来。")
    planned_effects.record_narrated_progress(
        db, session.id, session, module, player, [], pre_gen_seq=0,
    )
    entry = (session.world_state or {}).get("clue_ledger", {}).get("clue_3")
    assert entry is not None
    assert entry["status"] == "partial"        # 文本匹配只记「有所察觉」，不敢判成已掌握
    assert entry["discovered_by"] == [player.id]
    assert "clue_5" not in (session.world_state or {}).get("clue_ledger", {})


def test_narrated_progress_ignores_bare_mention(db_factory):
    """只在环境描写里带过一句「有块石板」不算发现——那时玩家还什么都没看到。"""
    db = db_factory()
    session, module, player = _seed_village(db)
    _narrate(db, session, "院子里横七竖八地躺着几块石板，荒草从缝隙间钻出来。")
    planned_effects.record_narrated_progress(
        db, session.id, session, module, player, [], pre_gen_seq=0,
    )
    assert not (session.world_state or {}).get("clue_ledger")


def test_narrated_progress_ignores_clues_elsewhere(db_factory):
    """别处的线索不因为一个同名词就被记掉（绘本在还没去过的树洞）。"""
    db = db_factory()
    session, module, player = _seed_village(db)
    _narrate(db, session, "香澄翻开那本绘本，纸页脆得像要碎掉。")
    planned_effects.record_narrated_progress(
        db, session.id, session, module, player, [], pre_gen_seq=0,
    )
    assert "clue_4" not in (session.world_state or {}).get("clue_ledger", {})


def test_narrated_progress_records_scene_event(db_factory):
    """叙事演过的场景机制点记进台账 → 下一轮 KP 看得见「已发生」，不会重演。"""
    db = db_factory()
    session, module, player = _seed_village(db)
    _narrate(db, session, "你把那张村规从墙上揭下来，借着手电光一行行读完。")
    planned_effects.record_narrated_progress(
        db, session.id, session, module, player, [], pre_gen_seq=0,
    )
    assert world_memory.scene_event_seen(session.world_state, "scene_8", 1)
    assert not world_memory.scene_event_seen(session.world_state, "scene_8", 0)


def test_narrated_progress_scene_event_matching_is_literal(db_factory):
    """能力边界：KP 把「被拖拽」整段改写掉时，文本匹配抓不住。

    实测那句是「那只手猛地一拽，你半个人被扯进门洞」——与 trigger 的「小屋 / 拖拽」
    没有一个字面重合。这类只能靠后端确定性发出机制时记的那本账，
    此处如实断言现状，免得日后误以为覆盖了语义改写。
    """
    db = db_factory()
    session, module, player = _seed_village(db)
    _narrate(db, session, "黑暗里那只手猛地一拽，你半个人被扯进门洞，鞋底在门槛上碾出一道深痕。")
    planned_effects.record_narrated_progress(
        db, session.id, session, module, player, [], pre_gen_seq=0,
    )
    assert not world_memory.scene_event_seen(session.world_state, "scene_8", 0)


def test_narrated_progress_does_not_downgrade_known(db_factory):
    """规划器已记成「完全掌握」的线索，不会被兜底的 hint 拉回 partial。"""
    db = db_factory()
    session, module, player = _seed_village(db)
    session.world_state = {"clue_ledger": {"clue_3": {"status": "known", "seq": 1}}}
    db.commit()
    _narrate(db, session, "你又低头看了看那块石板，刻痕还是那些刻痕。")
    planned_effects.record_narrated_progress(
        db, session.id, session, module, player, [], pre_gen_seq=0,
    )
    assert session.world_state["clue_ledger"]["clue_3"]["status"] == "known"


def test_narrated_progress_noop_without_new_narration(db_factory):
    """本轮没有新叙事（pre_gen_seq 之后无事件）→ 什么都不记。"""
    db = db_factory()
    session, module, player = _seed_village(db)
    _narrate(db, session, "你蹲下身，指腹贴着石板一寸寸扫过去。", seq=10)
    planned_effects.record_narrated_progress(
        db, session.id, session, module, player, [], pre_gen_seq=10,
    )
    assert not (session.world_state or {}).get("clue_ledger")


def test_narrated_progress_fail_open(db_factory):
    """记账异常绝不阻塞出牌（模组为 None 等残缺输入直接返回）。"""
    db = db_factory()
    session, _module, player = _seed_village(db)
    _narrate(db, session, "你蹲下身，指腹贴着石板一寸寸扫过去。")
    planned_effects.record_narrated_progress(
        db, session.id, session, None, player, [], pre_gen_seq=0,
    )
    assert not (session.world_state or {}).get("clue_ledger")


# ── v2：MemoryKeeper 差量合并（纯函数）─────────────────────────────


def _mem_ws() -> dict:
    return {
        "clue_ledger": {"clue_key": {"status": "partial", "note": "旧备注"}},
        "npc_memory": {"npc_butler": {
            "attitude": "neutral",
            "promises": ["答应带路"],
            "lies_told": ["谎称在厨房"],
            "interactions": [{"seq": 1, "summary": "初次照面"}],
        }},
    }


def test_apply_memory_delta_updates_attitude_and_appends():
    ws = world_memory.apply_memory_delta(
        _mem_ws(),
        npc_updates={"npc_butler": {
            "attitude": "wary",
            "attitude_reason": "被追问后神色慌张",
            "new_promises": ["答应带路", "答应半夜开西厢房门"],  # 前者已存在→去重
            "new_lies": ["谎称没听到动静"],
        }},
        clue_notes={"clue_key": "意识到暗格对应地下室"},
    )
    npc = ws["npc_memory"]["npc_butler"]
    assert npc["attitude"] == "wary"
    assert npc["attitude_reason"] == "被追问后神色慌张"
    assert npc["promises"] == ["答应带路", "答应半夜开西厢房门"]   # 追加去重保序
    assert npc["lies_told"] == ["谎称在厨房", "谎称没听到动静"]
    assert npc["interactions"] == [{"seq": 1, "summary": "初次照面"}]  # 环形缓冲不被触碰
    assert ws["clue_ledger"]["clue_key"]["note"] == "意识到暗格对应地下室"
    assert ws["clue_ledger"]["clue_key"]["status"] == "partial"       # status 恒不变


def test_apply_memory_delta_does_not_mutate_input():
    src = _mem_ws()
    world_memory.apply_memory_delta(
        src, npc_updates={"npc_butler": {"new_lies": ["新谎"]}},
    )
    assert src["npc_memory"]["npc_butler"]["lies_told"] == ["谎称在厨房"]


def test_apply_memory_delta_rejects_bad_attitude():
    ws = world_memory.apply_memory_delta(
        _mem_ws(), npc_updates={"npc_butler": {"attitude": "furious"}},
    )
    # 枚举外的态度视为幻觉丢弃，保持原态度
    assert ws["npc_memory"]["npc_butler"]["attitude"] == "neutral"


# ── v2：安全约束——台账 status 与不存在实体 ─────────────────────


def test_apply_memory_delta_ignores_clue_status_tampering():
    # 抽取器即便在 clue_notes 里塞 status/discovered，也只取 note，绝不改状态
    ws = world_memory.apply_memory_delta(
        _mem_ws(),
        clue_notes={"clue_key": {"status": "known", "note": "玩家已完全掌握"}},
    )
    assert ws["clue_ledger"]["clue_key"]["status"] == "partial"       # status 不变
    # value 是 dict（非字符串备注）经 _truncate 转成其 str 形态，仍不触碰 status 键
    assert "known" not in ws["clue_ledger"]["clue_key"].get("status", "")


def test_apply_memory_delta_ignores_unknown_npc_and_clue():
    ws = world_memory.apply_memory_delta(
        _mem_ws(),
        npc_updates={"npc_ghost": {"attitude": "hostile", "new_lies": ["瞎编"]}},
        clue_notes={"clue_nonexistent": "凭空线索"},
    )
    assert "npc_ghost" not in ws["npc_memory"]        # 不存在的 NPC 不新建
    assert "clue_nonexistent" not in ws["clue_ledger"]  # 不存在的线索不新建
    # 已存在实体不受牵连
    assert ws["npc_memory"]["npc_butler"]["attitude"] == "neutral"


# ── 纯函数：AI 队友私有记忆（team_memory）──────────────────────


def test_record_team_deed_ring_buffer():
    ws = {}
    for i in range(world_memory.MAX_TEAM_DEEDS + 3):
        ws = world_memory.record_team_deed(ws, "char_a", i, f"言行{i}")
    deeds = ws["team_memory"]["char_a"]["deeds"]
    assert len(deeds) == world_memory.MAX_TEAM_DEEDS       # 环形缓冲
    assert deeds[-1]["summary"] == f"言行{world_memory.MAX_TEAM_DEEDS + 2}"
    assert deeds[0]["summary"] == "言行3"                   # 最老的被挤掉
    # 空 id / 空摘要：no-op
    assert world_memory.record_team_deed({}, "", 1, "x") == {}
    assert world_memory.record_team_deed({}, "char_a", 1, "  ") == {}


def test_apply_team_memory_delta_whitelist_and_goals():
    allowed = {"char_a"}
    ws = world_memory.apply_team_memory_delta(
        {},
        {
            "char_a": {"new_goals": ["查明兄弟死因", "还哈桑的人情"], "new_notes": ["管家在撒谎"]},
            "char_ghost": {"new_goals": ["幻觉目标"]},   # 不在白名单：忽略
        },
        allowed,
    )
    mem = ws["team_memory"]["char_a"]
    assert mem["goals"] == ["查明兄弟死因", "还哈桑的人情"]
    assert mem["notes"] == ["管家在撒谎"]
    assert "char_ghost" not in ws["team_memory"]

    # done_goals 按精确文本移除；new_goals 去重
    ws = world_memory.apply_team_memory_delta(
        ws,
        {"char_a": {"new_goals": ["查明兄弟死因"], "done_goals": ["还哈桑的人情"]}},
        allowed,
    )
    assert ws["team_memory"]["char_a"]["goals"] == ["查明兄弟死因"]

    # goals 超上限丢最旧
    many = {"char_a": {"new_goals": [f"目标{i}" for i in range(world_memory.MAX_TEAM_GOALS + 2)]}}
    ws = world_memory.apply_team_memory_delta(ws, many, allowed)
    goals = ws["team_memory"]["char_a"]["goals"]
    assert len(goals) == world_memory.MAX_TEAM_GOALS
    assert "查明兄弟死因" not in goals                       # 最旧的被挤掉

    # deeds 绝不被差量触碰
    ws2 = world_memory.record_team_deed({}, "char_a", 1, "做了件事")
    ws2 = world_memory.apply_team_memory_delta(
        ws2, {"char_a": {"new_notes": ["一条心事"]}}, allowed,
    )
    assert ws2["team_memory"]["char_a"]["deeds"][0]["summary"] == "做了件事"


def test_format_team_self_memory_and_all_brief():
    ws = world_memory.apply_team_memory_delta(
        {}, {"char_a": {"new_goals": ["查明兄弟死因"], "new_notes": ["管家在撒谎"]}}, {"char_a"},
    )
    ws = world_memory.record_team_deed(ws, "char_a", 5, "说：我不信管家的话")

    text = world_memory.format_team_self_memory(ws, "char_a")
    assert "你当前的个人目标：查明兄弟死因" in text
    assert "管家在撒谎" in text and "我不信管家的话" in text
    assert world_memory.format_team_self_memory(ws, "char_b") == ""   # 无记忆不注入

    # all_brief：按队友清单遍历——没记忆的队友也要列出（否则抽取器建不起第一个目标）
    brief = world_memory.format_team_memory_all_brief(
        ws, {"char_a": "阿尔法", "char_b": "贝塔"},
    )
    assert "char_a（阿尔法）" in brief and "查明兄弟死因" in brief
    assert "char_b（贝塔）" in brief and "（暂无个人目标与心事）" in brief
    assert world_memory.format_team_memory_all_brief(ws, {}) == ""


# ── v2：合并调用形态（假 provider 桩，不调真实 LLM）──────────────


class _FakeLLM:
    """桩：complete 返回预置字符串或抛异常，记录最后一次调用参数。"""

    def __init__(self, resp=None, boom=False):
        self.resp = resp
        self.boom = boom
        self.last_kw = None
        self.last_messages = None

    async def complete(self, messages, temperature=0.7, **kw):
        self.last_kw = {"temperature": temperature, **kw}
        self.last_messages = messages
        if self.boom:
            raise RuntimeError("provider down")
        return self.resp


def _summ_events() -> list[EventLog]:
    return [EventLog(
        session_id="s", sequence_num=1, event_type="narration", content="管家闪烁其词。",
    )]


def test_summarize_and_extract_merged_shape():
    llm = _FakeLLM(json.dumps({
        "summary": "调查者审讯管家，他前后矛盾。",
        "npc_updates": {"npc_butler": {"attitude": "wary", "new_lies": ["谎称没出门"]}},
        "clue_notes": {"clue_key": "管家回避提及钥匙"},
    }))
    got = asyncio.run(story_summarizer.summarize_and_extract(
        llm, "既往摘要", _summ_events(), "- npc_butler：态度：中立",
    ))
    assert got is not None
    summary, npc_updates, clue_notes, team_updates = got
    assert "审讯管家" in summary                       # 摘要文本正确产出，未因抽取回归
    assert npc_updates["npc_butler"]["attitude"] == "wary"
    assert clue_notes == {"clue_key": "管家回避提及钥匙"}
    assert team_updates == {}                          # 未给 team_updates 时归一为空 dict
    # 合并调用是一次低温 json_object 调用
    assert llm.last_kw["temperature"] == 0
    assert llm.last_kw["response_format"] == {"type": "json_object"}


def test_summarize_and_extract_team_updates():
    """带 team_memory_brief 时：提示词包含队友差量任务，抽取结果第四元返回 team_updates。"""
    llm = _FakeLLM(json.dumps({
        "summary": "梗概。",
        "npc_updates": {},
        "clue_notes": {},
        "team_updates": {"char_a": {"new_goals": ["查明兄弟的死因"]}},
    }))
    got = asyncio.run(story_summarizer.summarize_and_extract(
        llm, "既往", _summ_events(), "",
        team_memory_brief="- char_a（阿尔法）：（暂无个人目标与心事）",
    ))
    assert got is not None
    assert got[3] == {"char_a": {"new_goals": ["查明兄弟的死因"]}}
    prompt = llm.last_messages[1]["content"]
    assert "team_updates" in prompt and "char_a（阿尔法）" in prompt

    # 不带 team_memory_brief（无 AI 队友）：提示词不出现队友差量任务
    llm2 = _FakeLLM(json.dumps({"summary": "梗概。", "npc_updates": {}, "clue_notes": {}}))
    got2 = asyncio.run(story_summarizer.summarize_and_extract(
        llm2, "既往", _summ_events(), "",
    ))
    assert got2 is not None and got2[3] == {}
    assert "team_updates" not in llm2.last_messages[1]["content"]


def test_summarize_and_extract_fail_open_on_exception():
    llm = _FakeLLM(boom=True)
    got = asyncio.run(story_summarizer.summarize_and_extract(
        llm, "既往", _summ_events(), "",
    ))
    assert got is None


def test_summarize_and_extract_fail_open_on_bad_json():
    llm = _FakeLLM("这不是 JSON，只是一段闲聊。")
    got = asyncio.run(story_summarizer.summarize_and_extract(
        llm, "既往", _summ_events(), "",
    ))
    assert got is None


def test_summarize_and_extract_empty_summary_is_none():
    llm = _FakeLLM(json.dumps({"summary": "", "npc_updates": {}, "clue_notes": {}}))
    got = asyncio.run(story_summarizer.summarize_and_extract(
        llm, "既往", _summ_events(), "",
    ))
    assert got is None


# ── v2：接线（_maybe_roll_story_summary 合并落库，带库）──────────


def _seed_long_session(db, n_events: int):
    """建一个已攒够摘要阈值的会话：n 条 narration 事件 + 一个 NPC 记忆种子。"""
    module = Module(
        title="记忆测试模组", rule_system="coc",
        npcs=[{"id": "npc_butler", "name": "老管家"}],
        clues=[{"id": "clue_key", "name": "书房钥匙"}],
    )
    player = Character(name="亨利", rule_system="coc")
    db.add_all([module, player])
    db.flush()
    session = GameSession(
        module_id=module.id, player_character_id=player.id, status="active",
        world_state={"npc_memory": {"npc_butler": {"attitude": "neutral"}},
                     "clue_ledger": {"clue_key": {"status": "partial"}}},
    )
    db.add(session)
    db.flush()
    for i in range(1, n_events + 1):
        db.add(EventLog(
            session_id=session.id, sequence_num=i, event_type="narration",
            # 触发按 token 算（不按条数），每条得是一段真实体量的旁白才攒得够阈值
            content=f"第{i}段旁白。" + "灯火在长廊尽头摇晃，墙上的影子跟着一同起伏，脚步声被地毯吃掉了大半。" * 13,
        ))
    db.commit()
    return session


def test_maybe_roll_story_summary_applies_delta(db_factory):
    db = db_factory()
    # 按 token 触发：50 条 × 约 300 字 ≈ 2.2 万估算 token，稳过阈值
    session = _seed_long_session(db, 50)
    llm = _FakeLLM(json.dumps({
        "summary": "剧情梗概正文。",
        "npc_updates": {"npc_butler": {"attitude": "wary",
                                       "new_promises": ["答应带路"]}},
        "clue_notes": {"clue_key": "补充备注"},
    }))
    asyncio.run(chat_service._maybe_roll_story_summary(db, session.id, llm))
    ws = session.world_state or {}
    assert ws.get("story_summary") == "剧情梗概正文。"           # 摘要落库
    assert ws["story_summary_seq"] > 0                          # 游标推进
    assert ws["npc_memory"]["npc_butler"]["attitude"] == "wary"  # 差量合并
    assert ws["npc_memory"]["npc_butler"]["promises"] == ["答应带路"]
    assert ws["clue_ledger"]["clue_key"]["note"] == "补充备注"
    assert ws["clue_ledger"]["clue_key"]["status"] == "partial"  # status 恒不变


def test_maybe_roll_story_summary_fail_open_keeps_memory(db_factory):
    db = db_factory()
    session = _seed_long_session(db, 50)
    before = dict(session.world_state or {})
    asyncio.run(chat_service._maybe_roll_story_summary(db, session.id, _FakeLLM(boom=True)))
    ws = session.world_state or {}
    # provider 抛异常：摘要不推进、NPC 记忆原样不变
    assert "story_summary" not in ws
    assert ws["npc_memory"] == before["npc_memory"]


def test_maybe_roll_story_summary_below_threshold_noop(db_factory):
    db = db_factory()
    session = _seed_long_session(db, 3)   # 远不够阈值
    called = {"n": 0}

    class _Counting(_FakeLLM):
        async def complete(self, messages, temperature=0.7, **kw):
            called["n"] += 1
            return await super().complete(messages, temperature, **kw)

    asyncio.run(chat_service._maybe_roll_story_summary(db, session.id, _Counting("{}")))
    assert called["n"] == 0                # 未攒够阈值：零 LLM 调用
    assert "story_summary" not in (session.world_state or {})


# ── 模组结局：抵达终局的确定性记录 ──────────────────────────────────

_ENDINGS = [{"id": "ending_a", "name": "结局A：冲出隧道", "when": "把油门推到底",
             "description": "电车冲出隧道"}]


def _ending_plan(reached: str):
    return turn_planner.TurnPlan(
        ending=turn_planner.EndingVerdict(reached_id=reached, reason="玩家把油门推到底"),
    )


def test_settle_plan_ending_records_and_backfills(db_factory):
    db = db_factory()
    session, module, player, mate = _seed(db)
    module.endings = _ENDINGS
    db.commit()
    plan = _ending_plan("ending_a")
    chat_service._settle_plan_ending(db, session.id, session, module, plan)
    reached = (session.world_state or {}).get("ending_reached")
    assert reached and reached["id"] == "ending_a" and "冲出隧道" in reached["name"]
    # 结局名/收场回填进 plan → KP 的裁定计划里才会有「本轮当终局演」那一段
    assert plan.ending.name == "结局A：冲出隧道" and plan.ending.description == "电车冲出隧道"
    # 落一条系统事件供玩家看到收束提示
    ev = db.query(EventLog).filter_by(session_id=session.id).all()[-1]
    assert (ev.metadata_ or {}).get("ending_reached") is True


def test_settle_plan_ending_ignores_hallucinated_id(db_factory):
    """规划器编出模组里没有的结局 id → 一律忽略，绝不据此收场。"""
    db = db_factory()
    session, module, player, mate = _seed(db)
    module.endings = _ENDINGS
    db.commit()
    plan = _ending_plan("ending_zzz")
    chat_service._settle_plan_ending(db, session.id, session, module, plan)
    assert not (session.world_state or {}).get("ending_reached")
    assert plan.ending.reached_id == ""     # 清掉，别让它渲染进 KP 指令


def test_settle_plan_ending_is_idempotent(db_factory):
    """已抵达过就不再重复记账/重复提示（后续回合仍可继续演尾声）。"""
    db = db_factory()
    session, module, player, mate = _seed(db)
    module.endings = _ENDINGS + [{"id": "ending_b", "name": "结局B"}]
    db.commit()
    chat_service._settle_plan_ending(db, session.id, session, module, _ending_plan("ending_a"))
    n = db.query(EventLog).filter_by(session_id=session.id).count()
    chat_service._settle_plan_ending(db, session.id, session, module, _ending_plan("ending_b"))
    assert (session.world_state or {}).get("ending_reached", {})["id"] == "ending_a"
    assert db.query(EventLog).filter_by(session_id=session.id).count() == n


def test_settle_plan_ending_noop_without_endings(db_factory):
    """模组没写结局 → 永不判定（存量模组维持原样）。"""
    db = db_factory()
    session, module, player, mate = _seed(db)
    chat_service._settle_plan_ending(db, session.id, session, module, _ending_plan("ending_a"))
    assert not (session.world_state or {}).get("ending_reached")


# ── 分层章节（LSM 式滚动摘要）──────────────────────────────


def test_章节为空时梗概为空串():
    assert world_memory.story_summary_text({}) == ""
    assert world_memory.story_chapters({}) == []


def test_旧单串存档视作第一章():
    """换成章节制不该让在途存档丢掉已有的摘要。"""
    ws = {"story_summary": "前情若干。", "story_summary_seq": 30}
    chapters = world_memory.story_chapters(ws)
    assert len(chapters) == 1
    assert chapters[0]["text"] == "前情若干。" and chapters[0]["to_seq"] == 30
    assert world_memory.story_summary_text(ws) == "前情若干。"


def test_追加章节不重写既有章节():
    """这是分层的全部意义：老章节逐字保留，误差不再逐次复利。"""
    ws = world_memory.append_story_chapter({}, "第一段剧情。", 1, 20)
    ws = world_memory.append_story_chapter(ws, "第二段剧情。", 21, 44)
    chapters = world_memory.story_chapters(ws)
    assert [c["text"] for c in chapters] == ["第一段剧情。", "第二段剧情。"]
    assert chapters[0]["from_seq"] == 1 and chapters[1]["to_seq"] == 44


def test_多章拼接带章节号_单章不带():
    ws = world_memory.append_story_chapter({}, "只有一段。", 1, 10)
    assert world_memory.story_summary_text(ws) == "只有一段。"
    ws = world_memory.append_story_chapter(ws, "第二段。", 11, 20)
    text = world_memory.story_summary_text(ws)
    assert "【第 1 章】只有一段。" in text and "【第 2 章】第二段。" in text


def test_追加是纯函数_不就地改():
    ws = {"other": 1}
    out = world_memory.append_story_chapter(ws, "一段。", 1, 10)
    assert world_memory.STORY_CHAPTERS_KEY not in ws
    assert out["other"] == 1


def test_空章节不追加():
    ws = world_memory.append_story_chapter({}, "   ", 1, 10)
    assert world_memory.story_chapters(ws) == []


def test_story_summary_字段保持同步():
    """前端「上下文占用」等旧读取口径仍看 story_summary，换章节制不该要求它们同步改。"""
    ws = world_memory.append_story_chapter({}, "一段。", 1, 10)
    assert ws["story_summary"] == world_memory.story_summary_text(ws)


def test_未超限时不触发下沉合并():
    ws = {}
    for i in range(world_memory.MAX_STORY_CHAPTERS):
        ws = world_memory.append_story_chapter(ws, f"第{i}段。", i * 10, i * 10 + 9)
    assert world_memory.chapters_to_merge(ws) == []


def test_超限时下沉最老的一批():
    ws = {}
    for i in range(world_memory.MAX_STORY_CHAPTERS + 1):
        ws = world_memory.append_story_chapter(ws, f"第{i}段。", i * 10, i * 10 + 9)
    head = world_memory.chapters_to_merge(ws)
    assert len(head) == world_memory.MERGE_CHAPTER_BATCH
    assert head[0]["text"] == "第0段。"          # 吃的是最老的，不是最新的


def test_合并替换保留_seq_区间与后续章节():
    ws = {}
    for i in range(5):
        ws = world_memory.append_story_chapter(ws, f"第{i}段。", i * 10, i * 10 + 9)
    out = world_memory.replace_merged_chapters(ws, "前三段的合并。", 3)
    chapters = world_memory.story_chapters(out)
    assert [c["text"] for c in chapters] == ["前三段的合并。", "第3段。", "第4段。"]
    assert chapters[0]["from_seq"] == 0 and chapters[0]["to_seq"] == 29  # 区间跨越被合并的三章


def test_合并失败时原样返回():
    """合并压坏了比章节多几段糟得多——LLM 没给出结果就保持原样。"""
    ws = world_memory.append_story_chapter({}, "一段。", 1, 10)
    assert world_memory.replace_merged_chapters(ws, "", 1) == ws
    assert world_memory.replace_merged_chapters(ws, "合并结果", 0) == ws
    assert world_memory.replace_merged_chapters(ws, "合并结果", 99) == ws


def test_浓缩落库走追加而非重写(db_factory):
    """接线验证：一次浓缩产出一章，既往章节逐字保留。"""
    db = db_factory()
    session = _seed_long_session(db, 50)
    # 先塞一章既有历史，模拟此前已浓缩过
    ws0 = world_memory.append_story_chapter(
        dict(session.world_state or {}), "既有的第一章。", 1, 5,
    )
    session.world_state = ws0
    db.commit()

    llm = _FakeLLM(json.dumps({"summary": "新的一章。", "npc_updates": {}, "clue_notes": {}},
                              ensure_ascii=False))
    asyncio.run(chat_service._maybe_roll_story_summary(db, session.id, llm))

    ws = db.get(GameSession, session.id).world_state or {}
    texts = [c["text"] for c in world_memory.story_chapters(ws)]
    assert texts == ["既有的第一章。", "新的一章。"]   # 老章节没被重写
    assert ws["story_summary_seq"] > 0
