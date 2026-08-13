"""导入期生成 NPC 称呼（aliases / unknown_as）：确定性收口与 fail-open。

语义判断（哪个字眼指人、哪个指房子）交给看得见整本模组的模型，这里只测**收口**——
模型给什么，系统采纳什么、丢弃什么。
"""

import copy
import json

import pytest

from app.services import module_service


class _LLM:
    def __init__(self, response):
        self.response = response
        self.messages = None

    async def complete(self, messages, **kwargs):
        self.messages = messages
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _install_llm(monkeypatch, payload):
    response = payload
    if not isinstance(payload, (str, Exception)):
        response = json.dumps(payload, ensure_ascii=False)
    llm = _LLM(response)
    monkeypatch.setattr(module_service, "get_fast_llm", lambda: llm)
    return llm


def _npcs():
    return [
        {"id": "n_knott", "name": "史蒂芬·诺特", "description": "房东，焦急，想出租房子。"},
        {"id": "n_corbitt", "name": "沃尔特·科比特", "description": "不死巫师，干枯如树皮的尸体。",
         "secrets": ["他就是这栋房子闹鬼的根源"]},
    ]


async def _run(monkeypatch, payload, npcs=None):
    llm = _install_llm(monkeypatch, payload)
    out = await module_service.generate_npc_callings(
        npcs if npcs is not None else _npcs(), ["科比特的老房子"], "鬼屋", "coc",
    )
    return out, llm


def test_候选称呼由全名机械拆出():
    """召回是确定性的：模型只做否决，不负责想起「诺特」这种最该有的单名。"""
    assert module_service._alias_candidates("史蒂芬·诺特") == ["史蒂芬", "诺特"]
    assert module_service._alias_candidates("田间潜随者（莎布·尼古拉丝化身）") == []
    assert module_service._alias_candidates("金·戴伯伦") == ["戴伯伦"]   # 单字「金」不作候选
    assert module_service._alias_candidates("鼠群") == []                # 没得拆


@pytest.mark.asyncio
async def test_候选留下_补充项追加(monkeypatch):
    out, _llm = await _run(monkeypatch, {"npcs": [
        {"id": "n_knott", "reject": [], "extra": ["诺特先生"], "unknown_as": "陌生男性"},
        {"id": "n_corbitt", "reject": [], "extra": [], "unknown_as": "不明存在"},
    ]})
    assert out[0]["aliases"] == ["史蒂芬", "诺特", "诺特先生"]
    assert out[0]["unknown_as"] == "陌生男性"
    assert out[1]["aliases"] == ["沃尔特", "科比特"]


@pytest.mark.asyncio
async def test_否决的候选不进别名(monkeypatch):
    """「科比特」是那栋老宅的名字：留着的话，KP 一提这栋房子，玩家就等于认出了
    住在里面的东西。"""
    out, _llm = await _run(monkeypatch, {"npcs": [
        {"id": "n_corbitt", "reject": ["科比特"], "extra": [], "unknown_as": "不明存在"},
    ]})
    assert out[1]["aliases"] == ["沃尔特"]


@pytest.mark.asyncio
async def test_丢弃单字与重复全名的补充项(monkeypatch):
    """单字别名在中文里随便一句话都撞得上；把全名重复写进来是白占位置。"""
    out, _llm = await _run(monkeypatch, {"npcs": [
        {"id": "n_knott", "reject": [], "unknown_as": "陌生男性",
         "extra": ["诺", "史蒂芬·诺特", "诺特先生", "诺特先生", "", "诺特"]},
    ]})
    assert out[0]["aliases"] == ["史蒂芬", "诺特", "诺特先生"]


@pytest.mark.asyncio
async def test_空的未识别称呼不采用(monkeypatch):
    """留空就回落到既有的推断，别把字段清成空串。"""
    npcs = [{"id": "n_knott", "name": "史蒂芬·诺特", "description": "房东。",
             "unknown_as": "陌生男性"}]
    out, _llm = await _run(monkeypatch, {"npcs": [
        {"id": "n_knott", "reject": [], "extra": ["诺特先生"], "unknown_as": ""},
    ]}, npcs=npcs)
    assert out[0]["unknown_as"] == "陌生男性"


@pytest.mark.asyncio
async def test_默认只补空缺_不覆盖已有的未识别称呼(monkeypatch):
    """实测重写有得有失：「鼠群」会被改成「不明存在」（老鼠一眼就看得出是老鼠），
    性别没写明的青年会被猜成「陌生男性」。补空缺是净收益，重写不是。"""
    npcs = [
        {"id": "n_rats", "name": "鼠群", "description": "十只左右的老鼠。", "unknown_as": "鼠群"},
        {"id": "n_knott", "name": "史蒂芬·诺特", "description": "房东。", "unknown_as": ""},
    ]
    payload = {"npcs": [
        {"id": "n_rats", "reject": [], "extra": [], "unknown_as": "不明存在"},
        {"id": "n_knott", "reject": [], "extra": ["诺特先生"], "unknown_as": "陌生男性"},
    ]}
    out, _llm = await _run(monkeypatch, payload, npcs=copy.deepcopy(npcs))
    assert out[0]["unknown_as"] == "鼠群"        # 已有值不动
    assert out[1]["unknown_as"] == "陌生男性"     # 空缺补上

    llm = _install_llm(monkeypatch, payload)
    forced = await module_service.generate_npc_callings(
        copy.deepcopy(npcs), [], "鬼屋", "coc", rewrite_unknown_as=True,
    )
    assert llm.messages is not None
    assert forced[0]["unknown_as"] == "不明存在"  # 显式开口才覆盖


@pytest.mark.asyncio
async def test_只喂名字与外貌_不给秘密(monkeypatch):
    """unknown_as 是玩家可见的称呼。把秘密摆在模型面前，它就会写出「不死恶魔」这种
    一句话揭底的遮罩——存量模组里确实有。没有材料，就编不出底。"""
    _out, llm = await _run(monkeypatch, {"npcs": []})
    prompt = llm.messages[0]["content"]
    assert "干枯如树皮的尸体" in prompt        # 外貌要给（判断像不像人靠它）
    assert "闹鬼的根源" not in prompt          # 秘密不给
    assert "科比特的老房子" in prompt          # 场景名单要给（判断哪些字眼是建筑名）


@pytest.mark.asyncio
async def test_未知id与坏条目一律忽略(monkeypatch):
    """乱给的裁决不影响任何人：每个 NPC 仍拿到自己的机械候选。"""
    out, _llm = await _run(monkeypatch, {"npcs": [
        {"id": "查无此人", "reject": ["史蒂芬"]}, "不是对象", {"extra": ["没有id"]},
    ]})
    assert out[0]["aliases"] == ["史蒂芬", "诺特"]
    assert out[1]["aliases"] == ["沃尔特", "科比特"]


@pytest.mark.asyncio
async def test_LLM异常时保留机械候选(monkeypatch):
    """有总比没有强——候选本来就是确定性的，不该被一次调用失败连累。
    留着的剧透风险由运行时的共用别名消歧再兜一道。"""
    out, _llm = await _run(monkeypatch, RuntimeError("boom"))
    assert out[0]["aliases"] == ["史蒂芬", "诺特"]
    assert out[0].get("unknown_as") in (None, "")   # 没裁决就不编 unknown_as


@pytest.mark.asyncio
async def test_没有NPC时不调模型(monkeypatch):
    llm = _install_llm(monkeypatch, {"npcs": []})
    assert await module_service.generate_npc_callings([], [], "空模组", "coc") == []
    assert llm.messages is None
