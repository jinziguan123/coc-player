"""``build_kp_context`` 的行为语料：金标准测试与重构对照共用。

``build_kp_context`` 是纯函数（不触数据库），所以语料只需要造 ORM 对象。
每条用例覆盖一组「哪些小节该出现 / 该缺席」的组合——上下文装配的绝大多数缺陷
都长这个样子：某个小节在某种局面下静默消失，或不该出现时出现了。
"""

from __future__ import annotations

from app.ai.context import build_kp_context
from app.models import Character, EventLog, GameSession, Module
from app.models.combat_state import CombatState

RULE_SYSTEM = "coc"


def _char(name: str, is_player: bool = True) -> Character:
    return Character(
        id=f"c-{name}", name=name, rule_system=RULE_SYSTEM, is_player=is_player,
        base_attributes={"力量": 50, "意志": 60}, skills={"侦查": 45},
        system_data={"hp": 11, "mp": 12, "san": 60}, backstory="旧书店店员。",
    )


def _module() -> Module:
    return Module(
        id="m-1", title="渡口来信", rule_system=RULE_SYSTEM,
        description="一封没有寄出的信。",
        world_setting={"player_brief": "你受雇于当铺老板，来查一封信的下落。"},
        scenes=[
            {"id": "s_open", "title": "委托与准备", "kind": "chapter",
             "description": "老板把信推过柜台。"},
            {"id": "s_dock", "title": "渡口", "kind": "location",
             "description": "木栈桥泡在雾里。", "connections": ["s_shop"]},
            {"id": "s_shop", "title": "当铺", "kind": "location",
             "description": "柜台后堆满旧钟。", "connections": ["s_dock"]},
        ],
        npcs=[
            {"id": "n_boss", "name": "当铺老板", "description": "花白胡子，手很稳。",
             "secrets": "他知道信里写了什么。"},
            {"id": "n_ferry", "name": "摆渡人", "description": "帽檐压得很低。"},
        ],
        clues=[
            {"id": "cl_letter", "name": "未寄出的信", "location": "s_shop",
             "content": "落款被水泡烂了。"},
            {"id": "cl_boat", "name": "船底的刻痕", "location": "s_dock",
             "content": "三道平行的划痕。"},
        ],
        triggers=[], handouts=[],
        truth="摆渡人二十年前就淹死了。",
    )


def _session(**kw) -> GameSession:
    base = dict(
        id="sess-1", module_id="m-1", status="active", current_scene_id="s_dock",
        world_state={"visited_scenes": ["s_open", "s_dock"]},
        narrative_style="", image_style="", rule_options={},
    )
    ws = kw.pop("world_state_extra", None)
    base.update(kw)
    if ws:
        base["world_state"] = {**base["world_state"], **ws}
    return GameSession(**base)


def _events(n: int = 3) -> list[EventLog]:
    kinds = [("narration", "KP", "雾从水面上漫过来。"),
             ("action", "陈守一", "我沿着栈桥往前走。"),
             ("dialogue", "当铺老板", "别去渡口。")]
    return [
        EventLog(session_id="sess-1", sequence_num=i + 1, event_type=k,
                 actor_name=a, content=c)
        for i, (k, a, c) in enumerate(kinds[:n])
    ]


def _mates() -> list[Character]:
    return [_char("林知微"), _char("坂田桐时")]


def cases() -> list[tuple[str, dict]]:
    """(用例名, build_kp_context 的 kwargs)。每条都自带独立的 session/module 实例。"""
    out: list[tuple[str, dict]] = []

    def add(name: str, **kw):
        base = dict(session=_session(), module=_module(), player_char=_char("陈守一"),
                    events=_events(), teammates=_mates())
        base.update(kw)
        out.append((name, base))

    add("常规回合")
    add("开场无事件", events=[])
    add("独自开团无队友", teammates=[])
    add("挂了规则书就广告查阅能力", rules_lookup_enabled=True)
    add("建了模组索引就广告原文检索", module_lookup_enabled=True)
    add("被动注入的模组原文摘录",
        module_excerpts=[{"text": "渡口的雾一年到头不散。"}, {"text": "老板姓周。"}])
    add("被动注入的规则要点",
        rule_excerpts=[{"text": "困难难度＝技能值的一半。"}])
    add("全队同处一地",
        scene_groups=[{"scene_id": "s_dock", "label": "渡口",
                       "members": ["陈守一", "林知微", "坂田桐时"]}])
    add("分头行动逐组构建上下文",
        viewer_scene_id="s_shop",
        scene_groups=[
            {"scene_id": "s_dock", "label": "渡口", "members": ["陈守一"]},
            {"scene_id": "s_shop", "label": "当铺", "members": ["林知微", "坂田桐时"]},
        ])
    add("村规与桌面约定", rule_options_block="## 本桌规矩\n- 幸运不可用于对抗检定。")
    add("玩家指定文风", session=_session(narrative_style="hardboiled"))
    combat_session = _session()
    combat_session.combat_state = CombatState(session_id="sess-1", state={
        "active": True, "round": 2, "turn_index": 0,
        "initiative": [
            {"id": "c-陈守一", "name": "陈守一", "is_player": True, "hp": 9, "max_hp": 11,
             "status": "ok"},
            {"id": "n_ferry", "name": "摆渡人", "is_player": False, "hp": 4, "max_hp": 12,
             "status": "重伤"},
        ],
    })
    add("战斗进行中", session=combat_session)
    add("战斗刚结束的余波",
        session=_session(world_state_extra={"combat_result": {
            "outcome": "players_win", "rounds": 3,
            "summary": "摆渡人被打散在雾里。",
        }}))
    add("线索台账全量渲染",
        session=_session(world_state_extra={"clue_ledger": {
            "cl_letter": {"status": "known"},
        }}))
    add("NPC 记忆",
        session=_session(world_state_extra={"npc_memory": {
            "n_boss": {"attitude": "戒备", "promises": ["答应过带你去渡口"],
                       "lies": ["说自己没见过摆渡人"], "recent": ["你追问了信的下落"]},
        }}))
    add("封路清单",
        session=_session(world_state_extra={"blocked_scenes": {
            "s_shop": "卷帘门从里面锁死了",
        }}))
    add("幕后推演产物",
        session=_session(world_state_extra={"backstage": {"cursor": 2}}))
    add("临场 NPC 已转正",
        session=_session(world_state_extra={"improvised_npcs": {
            "码头小贩": {"description": "卖热汤的瘸腿老头。"},
        }}))
    add("滚动摘要已推进游标就广告回想能力",
        recall_enabled=True,
        session=_session(world_state_extra={
            "story_summary": "调查员在当铺接下委托，随后前往渡口。",
            "story_summary_seq": 2,
        }))
    add("剧情 flag 已激活",
        session=_session(world_state_extra={"flags": {"met_ferryman": True}}))
    add("指定上下文预算", context_budget=20000)
    return out


CASES = cases()


def render(kwargs: dict) -> dict:
    """跑一次装配，取出可比对的形状（role 序列 + 每条消息全文）。"""
    msgs = build_kp_context(**kwargs)
    return {
        "roles": [m.get("role") for m in msgs],
        "messages": [
            {"role": m.get("role"), "content": str(m.get("content") or "")} for m in msgs
        ],
    }
