"""v3 登场事件的蛰伏闸门与离场兜底：用《闇暗山》香澄的真实结构做夹具。

线上回归：模组重解析为 v3 后，事件挪到了 ``module.events``，旧闸门只看 scenes 里的
kind=event，于是「靠事件登场」的香澄从开局就被当成在半山腰——幕后推演每轮让他躲在树后
观察，KP 照着写暗处的脚步声；小路上的登场桥段没演，KP 一个 scene_change 就去了旧神社，
香澄从此再没出场。
"""

from __future__ import annotations

from app.ai.agents import backstage_agent
from app.ai.context import _format_plot_events
from app.models.module import Module
from app.models.session import GameSession
from app.services import scene_kind, turn_effects

KASUMI = {
    "id": "npc_kasumi", "name": "香澄澪", "initial_location": "scene_fork",
    "secrets": ["已在山中困了十年"], "goals": ["保护探索者平安下山"],
}
YOBUKO = {"id": "npc_yobuko", "name": "呼子", "initial_location": "scene_hanging",
          "secrets": ["模仿人声引诱活人"]}

EVENTS = [
    {
        "id": "ev_kasumi_meet", "title": "香澄出现", "at": "scene_oldpath", "phase": "scene",
        "once": True, "npcs": ["npc_kasumi"],
        "when": {"text": "在原来的山路下山途中，一张苍白的脸出现在你眼前",
                 "expr": {"at": "scene_oldpath"}},
        "effects": [
            {"type": "sanity", "loss": "0/1", "targets": "actor"},
            {"type": "npc_state", "npc_id": "npc_kasumi", "alive": True,
             "location": "scene_oldpath"},
        ],
    },
    {
        "id": "ev_smallpath_cliff", "title": "光点与悬崖", "at": "scene_smallpath",
        "phase": "scene", "once": True, "npcs": ["npc_kasumi"],
        "when": {"text": "在小路上朝前方那点光走近时，突然被向后猛拉",
                 "expr": {"at": "scene_smallpath"}},
        "effects": [
            # 登场效果藏在检定分支里也要认得
            {"type": "check", "skill": "幸运", "on_success": [],
             "on_failure": [{"type": "npc_state", "npc_id": "npc_kasumi", "alive": True,
                             "location": "scene_smallpath"}]},
        ],
    },
    {
        # 香澄只是「参与」、不是登场：不应让他蛰伏
        "id": "ev_shrine_incident", "title": "神社草丛中的呼唤", "at": "scene_shrine",
        "phase": "scene", "once": True, "npcs": ["npc_yobuko", "npc_kasumi"],
        "when": {"text": "正要走向鸟居时草丛响动", "expr": {"at": "scene_shrine"}},
        "effects": [{"type": "sanity", "loss": "0/1", "targets": "actor"}],
    },
]

SCENES = [
    {"id": sid, "title": title, "kind": "location"}
    for sid, title in (
        ("scene_fork", "半山腰·岔路"), ("scene_oldpath", "原来的山路"),
        ("scene_smallpath", "小路"), ("scene_shrine", "旧神社"), ("scene_hanging", "吊死路"),
    )
]


def _module() -> Module:
    return Module(
        title="闇暗山", scenes=[dict(s) for s in SCENES], npcs=[dict(KASUMI), dict(YOBUKO)],
        events=[dict(e) for e in EVENTS], triggers=[], clues=[],
    )


def _session(scene: str = "scene_smallpath", **ws) -> GameSession:
    return GameSession(module_id="m", current_scene_id=scene, world_state=dict(ws))


class _DB:
    def __init__(self) -> None:
        self.commits = 0

    def commit(self) -> None:
        self.commits += 1


# ── 1. 蛰伏闸门认 v3 登场事件 ───────────────────────────────────


def test_登场事件含检定分支也能识别():
    got = scene_kind.entrance_events(EVENTS)
    assert set(got) == {"npc_kasumi"}
    assert [e["id"] for e in got["npc_kasumi"]] == ["ev_kasumi_meet", "ev_smallpath_cliff"]


def test_登场事件未发生前NPC蛰伏_只参与事件的NPC不蛰伏():
    dormant = scene_kind.module_dormant_npcs(_module(), set(), {})
    assert set(dormant) == {"npc_kasumi"}
    assert {e["id"] for e in dormant["npc_kasumi"]} == {"ev_kasumi_meet", "ev_smallpath_cliff"}


def test_任一登场事件发生后不再蛰伏():
    module = _module()
    assert scene_kind.module_dormant_npcs(module, set(), {"flow_seen": ["ev_smallpath_cliff"]}) == {}
    # 运行时已有它的状态（登场事件执行时写入）同样视为已登场
    assert scene_kind.module_dormant_npcs(
        module, set(), {"npc_runtime": {"npc_kasumi": {"location": "scene_oldpath"}}},
    ) == {}


def test_幕后推演不再替未登场的香澄行动():
    module = _module()
    session = _session("scene_fork")
    secret = backstage_agent.npcs_with_secrets(module)
    active, dormant = backstage_agent.split_dormant(module, secret, set(), session.world_state)
    assert [n["id"] for n in active] == ["npc_yobuko"]
    assert [n["id"] for n in dormant] == ["npc_kasumi"]


# ── 2. 离场兜底：必经登场桥段没演就先拦一次 ─────────────────────


def test_出发地有未演的登场事件_第一次切换被退回_再次切换放行():
    module, session, db = _module(), _session(), _DB()
    note = turn_effects._unresolved_entrance_refusal(
        db, session, module, "scene_smallpath", "scene_shrine",
    )
    assert "未切换场景" in note and "[BEAT: ev_smallpath_cliff]" in note and "香澄澪" in note
    assert session.world_state[turn_effects.LEAVE_WARNED_KEY] == ["scene_smallpath:ev_smallpath_cliff"]
    # KP 判断确实不成立、再切一次：放行，不会卡死
    assert turn_effects._unresolved_entrance_refusal(
        db, session, module, "scene_smallpath", "scene_shrine",
    ) == ""


def test_出发地没有登场事件或已登场时不拦():
    module, db = _module(), _DB()
    assert turn_effects._unresolved_entrance_refusal(
        db, _session("scene_fork"), module, "scene_fork", "scene_smallpath",
    ) == ""
    met = _session(flow_seen=["ev_kasumi_meet"])
    assert turn_effects._unresolved_entrance_refusal(
        db, met, module, "scene_smallpath", "scene_shrine",
    ) == ""
    assert db.commits == 0


def test_KP事件清单把当前可触发的登场事件标为必经():
    module = _module()
    text = _format_plot_events(module, _session(), set())
    line = next(ln for ln in text.splitlines() if "光点与悬崖" in ln)
    assert "必经登场桥段" in line and "香澄澪" in line
    shrine = next(ln for ln in text.splitlines() if "神社草丛中的呼唤" in ln)
    assert "必经" not in shrine
