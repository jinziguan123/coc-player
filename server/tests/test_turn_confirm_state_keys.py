"""回合确认只管理自己的键：清理旧顶层确认，不抹掉其他模块的回合状态。"""
from app.services.turn_state_service import _confirm_map, _write_confirm_map


def test_写确认表只清旧的顶层布尔确认键_保留其他模块状态():
    ts = {
        "c_old": True,                                   # 旧迁移铺在顶层的确认
        "turn_moves": [{"anchor": 1, "status": "pending_check"}],
        "pending_moves": [{"movers": ["a"]}],
        "auto_checks": [{"event_id": "e", "skill": "攀爬", "succeeded": True}],
        "pending_checks": [{"char_id": "a"}],
        "some_flag": False,
    }
    assert _confirm_map(ts) == {"c_old": True}
    out = _write_confirm_map(ts, {"c_new": True})
    assert out == {**{k: v for k, v in ts.items() if k != "c_old"}, "turn_confirm": {"c_new": True}}
    assert "c_old" in ts                                 # 输入不被修改
    assert _confirm_map(out) == {"c_new": True}
