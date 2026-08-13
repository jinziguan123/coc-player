"""KP 上下文的「队伍此刻的位置」小节：每轮全量渲染确定性位置，不让 KP 自己猜分头。

回归《鬼屋》那一局：全队都在街区（party_locations 归并只有一组，系统据此走单场景路径、
只跑一次生成），但上下文里只有一个全局「当前场景」、没有谁在哪，KP 便拿历史里几句
「我们分头吧」当成队伍已经分开，自行标出一个模组里不存在的「诺特的事务所」组，
整轮只演那一组——同轮另外三人的行动与一次侦查检定就此没有下文。
"""

import pytest

from app.ai.context import build_kp_context
from app.models import Character, EventLog, GameSession, Module
from app.services.turn_context import _location_groups


def _fixture():
    module = Module(
        title="鬼屋", rule_system="coc", description="", world_setting={},
        scenes=[
            {"id": "scene_1", "title": "委托与准备", "kind": "chapter"},
            {"id": "scene_6", "title": "街区", "kind": "location", "connections": []},
        ],
        npcs=[], clues=[], triggers=[], handouts=[],
    )
    session = GameSession(module_id="m", status="active", current_scene_id="scene_6",
                          world_state={"visited_scenes": ["scene_1", "scene_6"]})
    def _ch(name):
        return Character(name=name, rule_system="coc", is_player=True,
                         base_attributes={}, skills={}, system_data={})
    hero = _ch("陈守一")
    mates = [_ch("林知微"), _ch("坂田桐时"), _ch("莫妮卡·卡佩尔")]
    return module, session, hero, mates


def _sys(module, session, hero, mates, groups, **kw):
    # 非开场：位置小节只在游戏开始后注入（开场时还没人动过）
    events = [EventLog(session_id="s", sequence_num=1, event_type="narration",
                       content="夜色落下来了。", actor_name="KP")]
    msgs = build_kp_context(session, module, hero, events, teammates=mates,
                            scene_groups=groups, **kw)
    return "\n".join(str(m.get("content") or "") for m in msgs)


def test_全队同处一地_明写在一起并堵死意图分头():
    module, session, hero, mates = _fixture()
    groups = _location_groups(session, module, hero, mates)
    assert len(groups) == 1                      # 前提：确定性归并只有一组
    text = _sys(module, session, hero, mates, groups)
    assert "### 队伍此刻的位置" in text
    assert "- 街区：陈守一、林知微、坂田桐时、莫妮卡·卡佩尔" in text
    assert "全队都在一起" in text
    assert "那是打算，不是已经发生的事" in text   # 「说要分头」不等于已分头


def test_分头时逐组列出并点明本轮只演哪一组():
    module, session, hero, mates = _fixture()
    groups = [
        {"scene_id": "scene_6", "label": "街区", "members": ["陈守一", "坂田桐时"]},
        {"scene_id": "scene_1", "label": "委托与准备", "members": ["林知微"]},
    ]
    text = _sys(module, session, hero, mates, groups, viewer_scene_id="scene_1")
    assert "- 街区：陈守一、坂田桐时" in text
    assert "- 委托与准备：林知微" in text
    assert "队伍已分头" in text
    assert "只叙述【委托与准备】这一组" in text   # 聚焦哪一组按 viewer_scene_id 走
    assert "不要写别组的人此刻在做什么" in text


def test_不再教KP自己打GROUP标记():
    """分组由后端按 party_locations 确定性注入，KP 不必也不该自行宣布。"""
    module, session, hero, mates = _fixture()
    groups = _location_groups(session, module, hero, mates)
    text = _sys(module, session, hero, mates, groups)
    assert "[GROUP" not in text
    assert "分头行动（" not in text


@pytest.mark.parametrize("groups", [None, []])
def test_没算分组时整段不注入(groups):
    """调用方没给分组（旧调用点 / 单元测试）→ 行为与本特性上线前一致。"""
    module, session, hero, mates = _fixture()
    assert "队伍此刻的位置" not in _sys(module, session, hero, mates, groups)


def test_开场不注入():
    """开场时还没人动过，位置无从谈起，也没有历史可供误判。"""
    module, session, hero, mates = _fixture()
    groups = _location_groups(session, module, hero, mates)
    msgs = build_kp_context(session, module, hero, [], teammates=mates, scene_groups=groups)
    assert "队伍此刻的位置" not in "\n".join(str(m.get("content") or "") for m in msgs)
