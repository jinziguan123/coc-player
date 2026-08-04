"""队友只有对白时跳过二次 planner。

实测一个有两名 AI 队友的回合共 106 秒，其中二次 planner 占 46.2 秒（44%）——而那一轮
两个队友都只说了句话，planner 是对着一模一样的前提把玩家动作重判了一遍。
"""

from app.services.turn_orchestrator import _team_turn_changed_premises


class _Ev:
    def __init__(self, seq, etype, actor_id=None, actor_name=""):
        self.sequence_num, self.event_type = seq, etype
        self.actor_id, self.actor_name = actor_id, actor_name


class _Mate:
    def __init__(self, cid, name):
        self.id, self.name = cid, name


MATES = [_Mate("m1", "林知微"), _Mate("m2", "相马直树")]


def test_only_dialogue_skips_replan():
    """复现实测那一轮：玩家扯下便签，两个队友各接了一句话，没人动手。"""
    events = [
        _Ev(12, "action", "p1", "江户川龙牙"),          # 玩家宣言（在进度线之前）
        _Ev(13, "dialogue", "m1", "林知微"),
        _Ev(14, "dialogue", "m2", "相马直树"),
    ]
    assert _team_turn_changed_premises(events, MATES, pre_seq=12) is False


def test_teammate_action_forces_replan():
    """队友真动手 → 玩家动作的裁定前提变了，必须重规划。"""
    events = [
        _Ev(12, "action", "p1", "江户川龙牙"),
        _Ev(13, "dialogue", "m1", "林知微"),
        _Ev(14, "action", "m2", "相马直树"),           # 摸出手机看信号
    ]
    assert _team_turn_changed_premises(events, MATES, pre_seq=12) is True


def test_dice_or_scene_change_forces_replan():
    """掷骰、场景切换等系统事件不带队友署名，但同样是队友行动的产物 → 一律重规划。"""
    for etype in ("dice", "system", "combat", "scene_change"):
        events = [_Ev(12, "action", "p1"), _Ev(13, "dialogue", "m1", "林知微"),
                  _Ev(14, etype, None, "系统")]
        assert _team_turn_changed_premises(events, MATES, pre_seq=12) is True, etype


def test_events_before_the_mark_are_ignored():
    """进度线之前的事件是本轮之前就有的，不能据此误判成「队友动了」。"""
    events = [
        _Ev(10, "action", "m2", "相马直树"),           # 上一轮队友的行动
        _Ev(11, "dice", None, "系统"),                 # 上一轮的骰
        _Ev(12, "action", "p1", "江户川龙牙"),
        _Ev(13, "dialogue", "m1", "林知微"),
    ]
    assert _team_turn_changed_premises(events, MATES, pre_seq=12) is False


def test_no_teammates_never_replans():
    """单人局本来就不跑二次 planner。"""
    assert _team_turn_changed_premises([_Ev(13, "action", "p1")], [], pre_seq=12) is False


def test_player_own_action_after_the_mark_does_not_count_as_teammate():
    """判据只认队友的 action；玩家自己的补充发言不该触发重规划。"""
    events = [_Ev(12, "action", "p1", "江户川龙牙"), _Ev(13, "action", "p1", "江户川龙牙")]
    assert _team_turn_changed_premises(events, MATES, pre_seq=12) is False
