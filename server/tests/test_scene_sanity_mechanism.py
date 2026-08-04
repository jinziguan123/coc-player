"""场景 SAN 机制点的命中判据：trigger 说什么时候触发，就什么时候触发。

真实 bug：《常暗之箱》5 号车厢只有一条 SAN 机制、trigger 写着「阅读报纸时」，玩家刚拉开
隔门走进去就被扣了 SAN。成因是一条「该场景只有一条 SAN 机制 → 进场即当它触发」的兜底——
「只有一条」不构成把作者写死的触发条件改判成进场触发的理由。
"""

from app.services.planned_effects import _scene_sanity_mechanism


class _Module:
    def __init__(self, scenes):
        self.scenes = scenes


def _trigger_of(matched):
    return (matched or {}).get("trigger")


READING = {"trigger": "阅读报纸时", "kind": "san_check", "san_loss": "0/1"}
ENTERING = {"trigger": "进入房间时", "kind": "san_check", "san_loss": "1/1d3"}


def test_lone_conditional_mechanism_does_not_fire_on_entry():
    """场景里唯一那条机制写着「阅读报纸时」——进场不该触发它。

    宁可漏发（玩家真读报纸时 KP 仍可自发 [SAN_CHECK]），也不能凭空扣 SAN：
    SAN 是不可逆的，错扣一次玩家没法退回去。
    """
    module = _Module([{"id": "scene_5", "events": [READING]}])
    assert _scene_sanity_mechanism(
        module, "scene_5", "江户川龙牙拉开隔门，鞋底踩上两节车厢之间的接缝。", entered=True,
    ) is None


def test_conditional_mechanism_fires_when_its_trigger_actually_happens():
    """玩家真读了报纸 → 照常触发，功能本身没被削掉。"""
    module = _Module([{"id": "scene_5", "events": [READING]}])
    matched = _scene_sanity_mechanism(
        module, "scene_5", "他拿起座位上那份旧报纸读了起来", entered=False,
    )
    assert _trigger_of(matched) == "阅读报纸时"


def test_entry_marked_mechanism_still_fires_on_entry():
    """写明「进入…时」的机制点仍按进场补发——这条兜底是有价值的，不能一起砍掉。"""
    module = _Module([{"id": "s1", "events": [ENTERING]}])
    assert _trigger_of(_scene_sanity_mechanism(module, "s1", "他推门进去", entered=True)) == "进入房间时"


def test_entry_fallback_needs_exactly_one_entry_mechanism():
    """两条都写着进场时无法判定该用哪条，宁可不发。"""
    module = _Module([{"id": "s1", "events": [
        ENTERING, {"trigger": "走进地窖时", "kind": "san_check", "san_loss": "0/1"},
    ]}])
    assert _scene_sanity_mechanism(module, "s1", "他推门进去", entered=True) is None


def test_entry_and_conditional_coexist():
    """同一场景既有进场机制又有条件机制：进场只发进场那条，不误发条件那条。"""
    module = _Module([{"id": "s1", "events": [READING, ENTERING]}])
    assert _trigger_of(_scene_sanity_mechanism(module, "s1", "他推门进去", entered=True)) == "进入房间时"


def test_no_mechanism_or_no_scene_is_safe():
    module = _Module([{"id": "s1", "events": []}])
    assert _scene_sanity_mechanism(module, "s1", "任意描述", entered=True) is None
    assert _scene_sanity_mechanism(module, "不存在", "任意描述", entered=True) is None
    assert _scene_sanity_mechanism(None, "s1", "任意描述", entered=True) is None
