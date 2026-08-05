"""暂存的技能检定申请：推进时确定性取出，不靠 planner 从文本里再认一遍。

玩家在角色卡技能页点出来的申请，和大地图「前往」一样是本回合暂存动作。申请时把 skill
落进 metadata；推进时直接读 metadata。若改让 planner 从「（申请「侦查」检定）」这行行动
文本里认，认漏一次这次申请就被当成普通叙事顺过去了——玩家点的那一下等于没发生。
"""

from app.services.turn_orchestrator import _stashed_check_request


class _Ev:
    def __init__(self, metadata=None, actor_id=None):
        self.metadata_ = metadata or {}
        self.actor_id = actor_id


def _req(skill, actor_id="p1"):
    return _Ev({"pending_turn": True, "check_request": True, "skill": skill}, actor_id)


def test_no_stash_returns_empty():
    """一轮普通发言里没有申请 → 空串，走 planner 原有的裁定路径。"""
    turn = [_Ev({}, "p1"), _Ev({"travel": True, "scene_id": "b"}, "p1")]
    assert _stashed_check_request(turn, "p1") == ""


def test_picks_stashed_skill():
    turn = [_Ev({}, "p1"), _req("侦查")]
    assert _stashed_check_request(turn, "p1") == "侦查"


def test_last_one_wins():
    """同一轮点了多次只认最后一次——那是玩家改主意的自然语义。"""
    turn = [_req("侦查"), _req("聆听"), _req("图书馆使用")]
    assert _stashed_check_request(turn, "p1") == "图书馆使用"


def test_ignores_other_actors_request():
    """多人同桌：只认本轮行动者自己的申请，别把队友的申请安到他头上。"""
    turn = [_req("侦查", actor_id="p2"), _Ev({}, "p1")]
    assert _stashed_check_request(turn, "p1") == ""
    assert _stashed_check_request(turn, "p2") == "侦查"


def test_blank_skill_ignored():
    """技能名为空的脏数据不算一次申请（否则会把 requested_skill 顶成空串走进检定分支）。"""
    turn = [_Ev({"check_request": True, "skill": "  "}, "p1")]
    assert _stashed_check_request(turn, "p1") == ""
