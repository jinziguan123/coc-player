"""回合规划器的回归测试。

只验证结构化规划层，不依赖真实 LLM。
"""

import json
import logging

import pytest
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.ai import context as kp_context
from app.ai import turn_planner
from app.models import Base, Character, EventLog, GameSession, Module  # noqa: F401


def _payload(messages) -> dict:
    """从规划器 user 消息里稳健地抠出 payload JSON（不依赖指令文本里的换行数量）。"""
    content = messages[1]["content"]
    start = content.index("{")
    depth = 0
    for i in range(start, len(content)):
        if content[i] == "{":
            depth += 1
        elif content[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(content[start:i + 1])
    raise AssertionError("payload JSON 未找到")


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
        title="规划测试",
        rule_system="coc",
        scenes=[
            {"id": "hall", "name": "门厅", "description": "昏暗门厅"},
            {"id": "study", "name": "书房", "description": "尘封书房"},
        ],
        npcs=[
            {
                "id": "butler",
                "name": "管家",
                "description": "老管家",
                "personality": "谦卑",
                "secrets": ["知道地下室入口"],
                "initial_location": "hall",
            }
        ],
        clues=[
            {
                "id": "c1",
                "name": "书桌暗格",
                "description": "书桌内侧有一块松动的木板",
                "location": "study",
                "trigger_condition": "搜查书桌",
                "discovered": False,
            },
            {
                "id": "c2",
                "name": "地下室手记",
                "description": "被布遮住的手记",
                "location": "basement",
                "trigger_condition": "进入地下室",
                "discovered": False,
            },
        ],
        world_setting={},
    )
    hero = Character(name="调查员", rule_system="coc", is_player=True)
    db.add_all([module, hero])
    db.commit()
    session = GameSession(
        module_id=module.id,
        player_character_id=hero.id,
        status="active",
        current_scene_id="study",
        world_state={"visited_scenes": ["hall", "study"]},
    )
    db.add(session)
    db.commit()
    return module, hero, session


def test_turn_plan_messages_include_trigger_condition(db_factory):
    db = db_factory()
    module, hero, session = _seed(db)
    messages = turn_planner.build_turn_plan_messages(
        session,
        module,
        hero,
        [],
        teammates=[],
        rules_lookup_enabled=False,
    )
    text = "\n".join(m["content"] for m in messages)
    assert "搜查书桌" in text
    assert "书桌暗格" in text
    assert "地下室手记" not in text


def test_turn_plan_messages_include_canonical_scene_facts_without_exposing_clues(db_factory):
    """规划器须知道未访问场景的正典位置简述，但仍不能把其线索列为可揭示候选。"""
    db = db_factory()
    module, hero, session = _seed(db)
    messages = turn_planner.build_turn_plan_messages(session, module, hero, [])
    payload = _payload(messages)

    facts = {scene["id"]: scene for scene in payload["canonical_scene_facts"]}
    assert facts["hall"]["title"] == "门厅"
    assert facts["study"]["description"] == "尘封书房"
    assert "basement" not in {clue["location"] for clue in payload["visible_clues"]}
    assert "骰子只决定发现多少，不能改写世界原本是什么" in messages[1]["content"]


def test_compact_scenes_preserves_title_as_non_current_location_anchor():
    text = kp_context._compact_scenes(
        [
            {"id": "scene_6", "title": "6号车厢", "description": "起始车厢"},
            {"id": "scene_3", "title": "3号车厢", "description": "驾驶室钥匙掉落在此处"},
        ],
        "scene_6",
    )
    compact = json.loads(text)
    scene_3 = next(scene for scene in compact if scene["id"] == "scene_3")
    assert scene_3["name"] == "3号车厢"
    assert scene_3["description"] == "驾驶室钥匙掉落在此处"


def test_turn_plan_messages_include_characteristic_and_unstuck_hint(db_factory):
    """planner 指令须告知：check.skill 可用九维属性中文名；卡关时主动裁定灵感/教育检定解卡。"""
    db = db_factory()
    module, hero, session = _seed(db)
    messages = turn_planner.build_turn_plan_messages(
        session, module, hero, [], teammates=[], rules_lookup_enabled=False,
    )
    instruction = messages[1]["content"]
    assert "九维属性中文名" in instruction
    assert "灵感=智力" in instruction
    assert "卡关" in instruction and "解卡" in instruction
    assert "direction.nudge" in instruction


def test_turn_plan_messages_require_structured_combat_decision(db_factory):
    """规划器必须明确区分普通动作与开战，并返回可执行的敌方名单。"""
    db = db_factory()
    module, hero, session = _seed(db)
    messages = turn_planner.build_turn_plan_messages(
        session, module, hero, [], teammates=[], rules_lookup_enabled=False,
    )
    instruction = messages[1]["content"]
    assert "combat.should_start" in instruction
    assert "结构化战斗" in instruction
    assert "enemies" in instruction


def test_turn_plan_messages_apply_flag_resolved_npc_state(db_factory):
    """NPC 的位置/秘密可能因剧情 flag 变化（states 机制）。build_kp_context 会先按已激活
    flags 解析出『当前样貌』再喂给 KP；planner 必须看到同一份解析结果，否则会因为看着模组
    里的初始定义，把已经因剧情变化搬到别处、换了秘密的 NPC 判断错。"""
    db = db_factory()
    module = Module(
        title="状态测试",
        rule_system="coc",
        scenes=[
            {"id": "hall", "name": "门厅"},
            {"id": "study", "name": "书房"},
        ],
        npcs=[
            {
                "id": "butler",
                "name": "管家",
                "description": "老管家",
                "personality": "谦卑",
                "secrets": ["知道地下室入口"],
                "initial_location": "hall",
                "states": [
                    {
                        "when": ["butler_suspicious"],
                        "initial_location": "study",
                        "secrets": ["管家就是纵火者"],
                    }
                ],
            }
        ],
        clues=[],
        world_setting={},
    )
    hero = Character(name="调查员", rule_system="coc", is_player=True)
    db.add_all([module, hero])
    db.commit()
    session = GameSession(
        module_id=module.id,
        player_character_id=hero.id,
        status="active",
        current_scene_id="study",
        world_state={
            "visited_scenes": ["hall", "study"],
            "flags": ["butler_suspicious"],
        },
    )
    db.add(session)
    db.commit()

    messages = turn_planner.build_turn_plan_messages(
        session, module, hero, [], teammates=[], rules_lookup_enabled=False,
    )
    payload = _payload(messages)
    npc = next(n for n in payload["visible_npcs"] if n["id"] == "butler")
    assert npc["location"] == "study"  # 因 flag 已搬到书房，不是模组里定义的门厅
    assert npc["secrets"] == ["管家就是纵火者"]  # 秘密也随 flag 更新，不是初始定义


def test_turn_plan_messages_include_recent_actor_names(db_factory):
    db = db_factory()
    module, hero, session = _seed(db)
    event = EventLog(
        session_id=session.id,
        sequence_num=1,
        event_type="action",
        actor_id=hero.id,
        actor_name="调查员",
        content="我搜查书桌",
    )
    messages = turn_planner.build_turn_plan_messages(
        session,
        module,
        hero,
        [event],
        teammates=[],
        rules_lookup_enabled=False,
    )
    payload = _payload(messages)
    assert payload["recent_events"][0]["speaker"] == "调查员"
    assert payload["recent_events"][0]["content"] == "我搜查书桌"


@pytest.mark.asyncio
async def test_run_turn_planner_parses_json_plan():
    class _FakeLLM:
        async def complete(self, messages, temperature=0, response_format=None, max_tokens=None):
            return (
                '{"turn_kind":"investigate","player_intent":"调查书桌","requires_check":true,'
                '"check":{"skill":"侦查","difficulty":"normal","visibility":"open","reason":"结果不确定"},'
                '"clue_policy":{"action_matches_clue":true,"candidate_clue_ids":["c1"],"reveal_level":"basic",'
                '"requires_inspiration":false,"notes":"成功后给出暗格线索"},'
                '"npc_policy":{"speakers":["butler"],"reaction":"管家警觉","needs_npc_act":false},'
                '"scene_policy":{"scene_change":null,"set_flags":[],"clear_flags":[]},'
                '"combat":{"should_start":false,"enemies":[],"trigger":""},'
                '"narration_brief":["描述搜查动作","让管家插话阻拦"],'
                '"safety":{"do_not_reveal":["管家秘密"],"do_not_control_players":true}}'
            )

    messages = [{"role": "user", "content": "玩家正在搜查书桌"}]
    plan = await turn_planner.run_turn_planner(_FakeLLM(), messages)
    assert plan is not None
    assert plan.turn_kind == "investigate"
    assert plan.check.skill == "侦查"
    assert plan.clue_policy.candidate_clue_ids == ["c1"]
    assert plan.combat.should_start is False
    injected = turn_planner.build_turn_plan_message(plan)
    assert injected["role"] == "system"
    assert "调查书桌" in injected["content"]
    assert "管家秘密" in injected["content"]
    # 计划是内部工作稿：必须明确禁止把它的结构/字段名/内部 id 汇报体输出给玩家
    # （曾出现过 KP 把计划当成要念的报告，输出【场景状态更新】等标题+要点列表并泄露 flag 名）
    assert "内部工作稿" in injected["content"]
    assert "汇报体" in injected["content"]
    assert "do_not_reveal" in injected["content"]


def test_build_turn_plan_message_requires_check_硬约束():
    """requires_check=true 时，注入必须包含「照发的 [DICE_CHECK] 指令原文 + 不许提前泄结果」硬约束。

    这是评估回路里 plan_adherence 连续不过的根因修复：措辞太软时 KP 会把动作叙述
    「讲完」（敲出空层/摸到暗缝）却不发指令，既漏检定又提前泄露线索位置。
    """
    from app.ai.turn_planner import CheckPlan, TurnPlan

    plan = TurnPlan(
        requires_check=True,
        check=CheckPlan(skill="侦查", difficulty="normal", visibility="open"),
    )
    content = turn_planner.build_turn_plan_message(plan)["content"]
    # 必须把照发的指令原文喂给 KP（含技能名，open 明骰不赘写 visibility）
    assert "[DICE_CHECK: skill=侦查, difficulty=normal]" in content


def test_build_turn_plan_message_carries_group_check_scope():
    from app.ai.turn_planner import CheckPlan, TurnPlan

    content = turn_planner.build_turn_plan_message(
        TurnPlan(requires_check=True, check=CheckPlan(skill="幸运", chars="在场"))
    )["content"]
    assert "[DICE_CHECK: skill=幸运, difficulty=normal, chars=在场]" in content
    # 必须是「最后一行」硬约束，且明确禁止指令之前泄露结果/线索位置
    assert "最后一行" in content
    assert "凌驾叙事完整性" in content
    # 暗骰时 visibility 要写进指令原文
    blind_plan = TurnPlan(
        requires_check=True,
        check=CheckPlan(skill="心理学", difficulty="hard", visibility="blind"),
    )
    blind_content = turn_planner.build_turn_plan_message(blind_plan)["content"]
    assert "[DICE_CHECK: skill=心理学, difficulty=hard, visibility=blind]" in blind_content


def test_build_turn_plan_message_不需检定时无检定硬约束():
    """requires_check=false 时不注入检定硬约束，避免让 KP 无中生有地发检定。"""
    from app.ai.turn_planner import TurnPlan

    plan = TurnPlan(requires_check=False)
    content = turn_planner.build_turn_plan_message(plan)["content"]
    assert "本轮必须发起检定" not in content
    assert "[DICE_CHECK:" not in content


def test_build_turn_plan_message_开战时注入状态硬约束():
    from app.ai.turn_planner import CombatPlan, TurnPlan

    plan = TurnPlan(
        turn_kind="combat",
        player_intent="攻击循声者",
        combat=CombatPlan(
            should_start=True,
            enemies=["循声者"],
            trigger="调查员冲向循声者发动攻击",
        ),
    )
    content = turn_planner.build_turn_plan_message(plan)["content"]
    assert "结构化战斗切换" in content
    assert "必须调用 start_combat" in content
    assert "循声者" in content
    assert "确定性补偿" in content


def test_turn_plan_开战时取消普通检定():
    from app.ai.turn_planner import CheckPlan, CombatPlan, TurnPlan

    plan = TurnPlan(
        turn_kind="combat",
        requires_check=True,
        check=CheckPlan(skill="格斗(斗殴)"),
        combat=CombatPlan(should_start=True, enemies=["循声者"]),
    )
    assert plan.combat.should_start is True
    assert plan.requires_check is False


def test_auto_outcome_与检定互斥且非法值归一():
    """自动结局（success/failure）与掷骰互斥：置了自动结局就强制 requires_check=false；
    非法值归一为 none；开战优先，取消自动结局。"""
    from app.ai.turn_planner import CombatPlan, TurnPlan

    p = TurnPlan(requires_check=True, auto_outcome="failure")
    assert p.auto_outcome == "failure" and p.requires_check is False
    assert TurnPlan(auto_outcome="乱写").auto_outcome == "none"          # 非法→none
    # 模型常写 auto_outcome: null —— 必须容错为 none，绝不能让整份计划校验失败回退旧流程
    assert TurnPlan.model_validate({"auto_outcome": None}).auto_outcome == "none"
    assert TurnPlan.model_validate({"auto_outcome_reason": None}).auto_outcome_reason == ""
    p3 = TurnPlan(auto_outcome="success", combat=CombatPlan(should_start=True, enemies=["怪"]))
    assert p3.auto_outcome == "none"                                     # 开战取消自动结局


def test_标量str字段容忍dict_list_保住整份计划():
    """模型把自由文本标量字段写成 dict/list（如 player_intent={'actor':…,'intent':…}）时，
    必须就地转字符串、保住整份 TurnPlan，绝不能因一个字段撞 str 类型被整体丢弃回退旧流程。
    这是线上真实报错（player_intent 收到 dict）的回归。"""
    from app.ai.turn_planner import TurnPlan

    # 顶层 player_intent 写成 dict → 拼各值成句，内容不丢
    p = TurnPlan.model_validate({
        "player_intent": {"actor": "江户川龙牙", "intent": "驾驶或控制车体"},
        "requires_check": True,
    })
    assert isinstance(p.player_intent, str)
    assert "江户川龙牙" in p.player_intent and "驾驶或控制车体" in p.player_intent
    assert p.requires_check is True                    # 整份计划保住，其它字段照常

    # 顶层 auto_outcome_reason 写成 list → 拼接
    p2 = TurnPlan.model_validate({"auto_outcome_reason": ["已暴露", "仍想潜行"]})
    assert p2.auto_outcome_reason == "已暴露；仍想潜行"

    # 子模型内部标量 str（check.reason）写成 dict → 就地容错，不连累整份计划
    p3 = TurnPlan.model_validate({"check": {"skill": "聆听", "reason": {"why": "隔墙有声"}}})
    assert p3.check.skill == "聆听" and "隔墙有声" in p3.check.reason


def test_sanity_loss_容忍整数与null():
    """SAN 损失字段是「骰式/数字」，模型常写成 int 0/1 或 null —— 必须 str 化容错，
    否则整份计划因 str 类型校验失败回退旧流程（丢掉全部裁定信号，评测里已复现）。"""
    from app.ai.turn_planner import TurnPlan

    p = TurnPlan.model_validate({"sanity": {"trigger": True, "success_loss": 0, "failure_loss": 1}})
    assert p.sanity.success_loss == "0" and p.sanity.failure_loss == "1"
    p2 = TurnPlan.model_validate({"sanity": {"success_loss": None, "failure_loss": None}})
    assert p2.sanity.success_loss == "0" and p2.sanity.failure_loss == "1d6"   # None→字段默认


def test_build_turn_plan_message_注入自动结局硬约束():
    """auto_outcome=failure 时注入「直接失败、据此确定性叙述、绝不写成侥幸成功」的硬约束（含入戏缘由）。"""
    from app.ai.turn_planner import TurnPlan

    plan = TurnPlan(auto_outcome="failure",
                    auto_outcome_reason="手机巨响已把循声者引到玩家位置，行踪彻底暴露")
    content = turn_planner.build_turn_plan_message(plan)["content"]
    assert "自动结局" in content and "直接失败" in content
    assert "循声者" in content                                            # 入戏缘由被带上
    assert "绝不能" in content                                            # 明确禁止写成侥幸成功
    # success 走另一支：兑现为实打实进展
    ok = turn_planner.build_turn_plan_message(
        TurnPlan(auto_outcome="success", auto_outcome_reason="话术切中动机且承诺保密"))["content"]
    assert "直接成功" in ok
    # none（默认）不注入
    assert "自动结局" not in turn_planner.build_turn_plan_message(TurnPlan())["content"]


def test_turn_plan_prompt_含裁定准则与原型例():
    """规划器提示必须给出「虚构态势→难度/奖惩骰/免检」的裁定准则与两条原型例，
    否则 auto_outcome / bonus / penalty 只是无人会用的死字段。"""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    module = Module(title="M", rule_system="coc", npcs=[], scenes=[])
    pc = Character(name="亨利", rule_system="coc", is_player=True, system_data={})
    db.add_all([module, pc]); db.flush()
    s = GameSession(module_id=module.id, player_character_id=pc.id, status="active",
                    world_state={}, current_scene_id=None)
    db.add(s); db.commit()
    ev = EventLog(session_id=s.id, sequence_num=1, event_type="action",
                  actor_id=pc.id, actor_name="亨利", content="我想潜行")
    db.add(ev); db.commit()
    msgs = turn_planner.build_turn_plan_messages(s, module, pc, [ev])
    text = "".join(m["content"] for m in msgs)
    assert "裁定准则" in text and "auto_outcome" in text
    assert "循声" in text and "话术" in text          # 两条原型例都在
    assert "别让口才碾平一切" in text                  # 高风险防滥用护栏


class _RawLLM:
    """按预设原始字符串/对象作 complete 返回值的桩 LLM。"""
    def __init__(self, raw):
        self._raw = raw

    async def complete(self, messages, temperature=0, response_format=None, max_tokens=None):
        return self._raw


@pytest.mark.asyncio
async def test_run_turn_planner_tolerates_dirty_json():
    """模型不严格遵守 JSON 约定时也要能解析（这直接关系 KP 裁定约束的稳定性）：
    ```json 围栏、前后夹解释文字、无语言标注围栏、以及已是 dict 的返回都应成功。"""
    valid = '{"turn_kind":"social","player_intent":"盘问管家"}'
    dirty = [
        "```json\n" + valid + "\n```",
        "这是本轮裁定计划：\n" + valid + "\n（以上）",
        "```\n" + valid + "\n```",
    ]
    for raw in dirty:
        plan = await turn_planner.run_turn_planner(_RawLLM(raw), [])
        assert plan is not None, raw
        assert plan.turn_kind == "social" and plan.player_intent == "盘问管家"

    # provider 已解析成 dict → 直接可用
    plan = await turn_planner.run_turn_planner(_RawLLM({"turn_kind": "combat"}), [])
    assert plan is not None and plan.turn_kind == "combat"


@pytest.mark.asyncio
async def test_run_turn_planner_fails_open_on_unparseable():
    """空/纯文本/无大括号 → 无法解析 JSON → 回退 None，不阻塞跑团。"""
    for junk in ["", "抱歉，我无法生成计划", "no braces here"]:
        assert await turn_planner.run_turn_planner(_RawLLM(junk), []) is None


@pytest.mark.asyncio
async def test_run_turn_planner_tolerates_bad_field_shapes():
    """合法 JSON 但个别字段形状/枚举写错 → 归一而非丢弃整份计划（否则次要字段拖垮核心裁定）。"""
    plan = await turn_planner.run_turn_planner(
        _RawLLM('{"turn_kind":"invalid_kind","safety":"安全，无威胁"}'), [],
    )
    assert plan is not None
    assert plan.turn_kind == "mixed"  # 非法枚举退默认
    assert plan.safety.do_not_reveal == []  # 句子形状的 safety 退默认


@pytest.mark.asyncio
async def test_run_turn_planner_tolerates_null_fields():
    """模型用 null 表达「这项没有」→ 退字段默认，而不是丢掉整份计划。

    这是线上实际打爆过的路子：规划器每轮如实回 ``"ending": {"reached_id": null}``
    （本轮没到结局），撞上纯 str 字段，整份 TurnPlan 校验失败回退旧流程——
    日志里只有一行 WARNING，规划器就这么整局静悄悄地没生效。
    """
    raw = (
        '{"turn_kind":"investigate","player_intent":"搜查书桌",'
        '"ending":{"reached_id":null,"reason":null},'
        '"check":{"skill":"侦查","bonus":null},'
        '"npc_policy":{"speakers":null},'
        '"auto_outcome":null,"requires_check":null}'
    )
    plan = await turn_planner.run_turn_planner(_RawLLM(raw), [])
    assert plan is not None
    assert plan.turn_kind == "investigate"          # 计划本体完好，没被次要字段拖垮
    assert plan.player_intent == "搜查书桌"
    assert plan.ending.reached_id == ""             # null → 字段默认
    assert plan.check.bonus == 0
    assert plan.check.skill == "侦查"
    assert plan.auto_outcome == "none"
    assert plan.requires_check is False
    assert plan.npc_policy.speakers == []


@pytest.mark.asyncio
async def test_bool_on_non_bool_field_falls_back_to_default():
    """模型用 false 回答「要不要换场景」，而 scene_change 要的是场景 id。

    线上第二次打爆规划器的就是这个：``"scene_change": false`` 撞 str | None。
    false 不含可用内容（转成字符串只会得到没意义的 "False"），退默认才是它的本意。
    """
    plan = await turn_planner.run_turn_planner(
        _RawLLM('{"turn_kind":"move","player_intent":"去书房",'
                '"scene_policy":{"scene_change":false}}'), [],
    )
    assert plan is not None
    assert plan.turn_kind == "move" and plan.player_intent == "去书房"
    assert plan.scene_policy.scene_change is None


@pytest.mark.asyncio
async def test_unknown_type_errors_drop_only_that_field(caplog):
    """兜底：没有专门补丁的类型错，也只该丢那一个字段，不该丢整份计划。

    LLM 能写错的类型是穷举不完的，逐个打补丁永远慢一步，而每次漏网的代价
    都是**整份**裁定计划作废、KP 静悄悄回退旧流程。这里用四种没单独处理过的
    错法（str→int、list→int、str→bool）验证兜底真的兜得住。
    """
    raw = (
        '{"turn_kind":"social","player_intent":"盘问管家",'
        '"check":{"skill":"话术","bonus":"很多","penalty":[1,2]},'
        '"sanity":{"trigger":"也许"},"combat":{"should_start":"不确定"}}'
    )
    with caplog.at_level(logging.WARNING):
        plan = await turn_planner.run_turn_planner(_RawLLM(raw), [])
    assert plan is not None
    # 核心裁定信号完好
    assert plan.turn_kind == "social"
    assert plan.player_intent == "盘问管家"
    assert plan.check.skill == "话术"
    # 坏字段各自退默认
    assert plan.check.bonus == 0 and plan.check.penalty == 0
    assert plan.sanity.trigger is False
    assert plan.combat.should_start is False
    # 丢了什么要在日志里点名，否则又是一次静悄悄的降级
    for field in ("check.bonus", "check.penalty", "sanity.trigger", "combat.should_start"):
        assert field in caplog.text


@pytest.mark.asyncio
async def test_clean_plan_logs_nothing(caplog):
    """字段都对时不该有任何告警——兜底不能把正常回合也说成有问题。"""
    with caplog.at_level(logging.WARNING):
        plan = await turn_planner.run_turn_planner(
            _RawLLM('{"turn_kind":"combat","player_intent":"拔枪"}'), [],
        )
    assert plan is not None and plan.turn_kind == "combat"
    assert "类型对不上" not in caplog.text


@pytest.mark.asyncio
async def test_null_is_kept_for_optional_fields():
    """声明成 ``str | None`` 的字段收得下 null，别一并抹掉——
    scene_policy.scene_change 用 None 表示「不换场景」，和空串不是一回事。"""
    plan = await turn_planner.run_turn_planner(
        _RawLLM('{"turn_kind":"move","scene_policy":{"scene_change":null}}'), [],
    )
    assert plan is not None
    assert plan.scene_policy.scene_change is None


def test_submodel_tolerance_covers_every_submodel():
    """子模型容错必须覆盖 TurnPlan 上的每一个子模型字段。

    这条断言存在的理由：这份覆盖范围原先是手写清单，漏了 ending 一个——
    漏掉的字段不会报错，只会悄悄失去容错。改成从模型推导后，这里钉住它不再退回手写。
    """
    subs = [
        name for name, f in turn_planner.TurnPlan.model_fields.items()
        if isinstance(f.annotation, type) and issubclass(f.annotation, BaseModel)
    ]
    assert len(subs) >= 10
    for name in subs:
        # 每个子模型字段被写成一句话（LLM 常见错误）都应退默认，而非丢弃整份计划
        plan = turn_planner.TurnPlan.model_validate({"turn_kind": "social", name: "没有"})
        assert plan.turn_kind == "social", f"{name} 形状错误拖垮了整份计划"
        # null 同理
        plan = turn_planner.TurnPlan.model_validate({"turn_kind": "social", name: None})
        assert plan.turn_kind == "social", f"{name}=null 拖垮了整份计划"


def test_turn_plan_messages_include_truth_and_scene_events(db_factory):
    """payload 带模组幕后真相与当前场景机制点（events）；指令要求命中时数值照抄、不得估值。"""
    db = db_factory()
    module, hero, session = _seed(db)
    module.truth = "真相：管家杀害了主人并伪装成意外。"
    scenes = [dict(s) for s in module.scenes]
    for s in scenes:
        if s.get("id") == "study":
            s["events"] = [{"trigger": "翻动书桌后的尸体", "kind": "san_check", "san_loss": "0/1d3"}]
    module.scenes = scenes
    db.commit()

    messages = turn_planner.build_turn_plan_messages(
        session, module, hero, [], teammates=[], rules_lookup_enabled=False,
    )
    text = "\n".join(m["content"] for m in messages)
    assert "管家杀害了主人" in text                    # truth 进 payload
    assert "0/1d3" in text and "翻动书桌后的尸体" in text  # 当前场景 events 进 payload
    instruction = messages[1]["content"]
    assert "机制点" in instruction and "照抄" in instruction


def test_planner_payload_带随身物品清单(db_factory):
    """没有库存，规划器无从判断「我掏出灯塔备用钥匙」是真有还是现编，只能装看不见。"""
    db = db_factory()
    module, hero, session = _seed(db)
    hero.system_data = {
        **(hero.system_data or {}),
        "inventory": [{"id": "i1", "name": "手电筒", "qty": 1, "kind": "gear"},
                      {"id": "i2", "name": "火柴", "qty": 3, "kind": "consumable"}],
    }
    db.commit()

    text = "\n".join(m["content"] for m in turn_planner.build_turn_plan_messages(
        session, module, hero, [], teammates=[], rules_lookup_enabled=False,
    ))
    assert "手电筒" in text and "火柴" in text
    assert "false_claim" in text          # 字段说明在册
    assert "requires_check=true 时" in text  # 未决检定不预发收益


def test_build_turn_plan_message_注入虚假声称硬约束():
    """玩家声称了身上没有的东西 → KP 必须当场否掉，装看不见是最坏的处理。"""
    plan = turn_planner.TurnPlan(
        false_claim="玩家声称掏出灯塔备用钥匙，但其随身物品里没有钥匙",
    )
    content = turn_planner.build_turn_plan_message(plan)["content"]
    assert "灯塔备用钥匙" in content
    assert "当场否掉" in content and "装看不见" in content

    # 没有虚假声称时不注入这一段，别白占上下文
    assert "当场否掉" not in turn_planner.build_turn_plan_message(turn_planner.TurnPlan())["content"]


def test_虚假声称段不抢走检定硬约束的末尾位置():
    """check_block 靠「上下文最末尾」换取照发 [DICE_CHECK] 的遵循率，不能被别的段落挤掉尾巴。"""
    content = turn_planner.build_turn_plan_message(turn_planner.TurnPlan(
        requires_check=True,
        check=turn_planner.CheckPlan(skill="敏捷"),
        false_claim="玩家声称掏出灯塔备用钥匙，但其随身物品里没有钥匙",
    ))["content"]
    assert content.index("当场否掉") < content.index(turn_planner.REQUIRES_CHECK_MARKER)
    assert content.rstrip().endswith("]")   # 仍以那行检定指令收尾


def test_planner_payload_把武器一并算进随身清单(db_factory):
    """武器存在 system_data.weapons 而非 inventory；漏了它，规划器会把角色卡上真有的撬棍
    当成玩家现编的东西填进 false_claim——比不否更糟。"""
    db = db_factory()
    module, hero, session = _seed(db)
    hero.system_data = {
        **(hero.system_data or {}),
        "inventory": [{"id": "i1", "name": "手电筒", "qty": 1}],
        "weapons": [{"name": "撬棍", "skill": "斗殴", "success": 45, "dam": "1d8"}],
    }
    db.commit()

    text = "\n".join(m["content"] for m in turn_planner.build_turn_plan_messages(
        session, module, hero, [], teammates=[], rules_lookup_enabled=False,
    ))
    assert "撬棍" in text and "手电筒" in text


# ── 模组结局：判定「已抵达终局」的机制信号 ──────────────────────────────

_ENDINGS = [
    {"id": "ending_a", "name": "结局A：冲出隧道", "when": "把油门拉杆推到底让电车加速",
     "description": "电车撞碎黑暗冲出隧道，幸存者在晨光里瘫坐"},
    {"id": "ending_b", "name": "结局B：停在黑暗里", "when": "拉下拉杆减速停车"},
]


def test_turn_plan_messages_include_endings(db_factory):
    """结局条件要进规划器输入——否则玩家拉下加速杆和推开一扇门在系统眼里没有区别。"""
    db = db_factory()
    module, hero, session = _seed(db)
    module.endings = _ENDINGS
    db.commit()
    text = "\n".join(m["content"] for m in turn_planner.build_turn_plan_messages(
        session, module, hero, [], teammates=[], rules_lookup_enabled=False,
    ))
    assert "把油门拉杆推到底让电车加速" in text
    assert "ending_a" in text


def test_turn_plan_messages_drop_endings_once_reached(db_factory):
    """已经抵达过结局就不再重复判定，省得每轮都问一遍。"""
    db = db_factory()
    module, hero, session = _seed(db)
    module.endings = _ENDINGS
    session.world_state = dict(session.world_state or {}) | {
        "ending_reached": {"id": "ending_a", "name": "结局A：冲出隧道"},
    }
    db.commit()
    text = "\n".join(m["content"] for m in turn_planner.build_turn_plan_messages(
        session, module, hero, [], teammates=[], rules_lookup_enabled=False,
    ))
    assert "把油门拉杆推到底让电车加速" not in text


def test_plan_directive_renders_ending_block():
    """抵达终局要在 KP 的裁定计划里成为一段显式指令，不能只在系统内部记一笔。"""
    plan = turn_planner.TurnPlan(
        ending=turn_planner.EndingVerdict(
            reached_id="ending_a", reason="玩家把油门推到底",
            name="结局A：冲出隧道", description="电车冲出隧道",
        ),
    )
    text = turn_planner.build_turn_plan_message(plan)["content"]
    assert "已抵达终局" in text and "结局A：冲出隧道" in text
    assert "不要替玩家宣布本模组结束" in text


def test_plan_directive_without_ending_has_no_block():
    text = turn_planner.build_turn_plan_message(turn_planner.TurnPlan())["content"]
    assert "已抵达终局" not in text


# ── 输出被截断：抢救已经写完的字段，别整份丢掉 ────────────────────────

# 线上日志里的真实片段：服务端输出上限把 JSON 截在了半截键 "auto_ 上。
_TRUNCATED = """{
  "intent": "调查大型建筑内神龛，比较其与祠堂神龛的差异，并尝试读取村规",
  "requires_check": true,
  "check": {
    "skill": "侦查",
    "difficulty": "normal",
    "reason": "神龛细致的刻痕与文字需要认真观察才能辨明",
    "chars": []
  },
  "auto_"""


@pytest.mark.asyncio
async def test_truncated_plan_is_salvaged_not_discarded():
    """被截断的计划要抢救出前面写完的字段，而不是回退旧流程把整轮裁定丢掉。

    此前这种输出整份丢弃：这一轮的检定裁定、线索记账、SAN 判断、安全边界全没了，
    而 intent/requires_check/check 明明已经写完、完全可用。
    """
    class _FakeLLM:
        async def complete(self, messages, temperature=0, response_format=None, max_tokens=None):
            return _TRUNCATED

    plan = await turn_planner.run_turn_planner(_FakeLLM(), [{"role": "user", "content": "x"}])
    assert plan is not None
    assert plan.requires_check is True
    assert plan.check.skill == "侦查"          # 检定裁定被救回来了
    assert plan.combat.should_start is False   # 没写到的字段走默认值


def test_repair_handles_truncation_anywhere():
    def ex(t):
        return turn_planner._extract_json_object(t, salvage_truncated=True)
    assert ex('{"a": 1, "b": "没写完的句子') == {"a": 1}            # 断在字符串中间
    assert ex('{"a": 1, "list": ["x", "y", "z') == {"a": 1, "list": ["x", "y"]}
    assert ex('{"a": 1, "o": {"p": 2, "q": {"r": 3, "s') == {"a": 1, "o": {"p": 2, "q": {"r": 3}}}


def test_repair_does_not_touch_healthy_or_hopeless_output():
    def ex(t):
        return turn_planner._extract_json_object(t, salvage_truncated=True)
    assert ex('{"a": 1, "b": 2}') == {"a": 1, "b": 2}   # 完整输出照旧
    assert ex('{"a') is None                            # 一个字段都没写完 → 仍回退
    assert ex("hello world") is None                    # 压根不是 JSON
    assert ex('{"a": "含 \\" 转义引号的值", "b": 2, "c') == {"a": '含 " 转义引号的值', "b": 2}
    # 默认不抢救：只有明确开了 salvage 的调用方才拿半份结果（校验器等成对结果不能开）
    assert turn_planner._extract_json_object('{"a": 1, "b') is None
