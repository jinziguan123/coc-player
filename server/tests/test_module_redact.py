"""防剧透自检：玩家可见的三段文本对照真相洗一遍（打桩，不打真实接口）。

实测泄漏样本取自「分离焦虑」：简介写「闯入神话污染的近亲繁殖农场」，而真相开头正是
「法恩斯沃斯家族是一个受到神话污染的亚人隐士种族，因近亲繁殖…」。
"""

import asyncio
import json

import pytest

from app.services import module_service as ms


class _LLM:
    def __init__(self, reply):
        self.reply = reply
        self.prompt = None

    async def complete(self, messages, **kw):
        self.prompt = messages[-1]["content"]
        return self.reply


def _parsed():
    return {
        "description": "调查员寻找失踪女博士，闯入神话污染的近亲繁殖农场",
        "intro": "现代，美国俄亥俄州乡村。田园的宁静掩盖着地窖深处的哀嚎。",
        "player_brief": "你们正在调查贝尔博士的失踪案。她三周前在停车场失踪。",
        "truth": "法恩斯沃斯家族是一个受到神话污染的亚人隐士种族，因近亲繁殖而被驱逐。",
        "npcs": [{"name": "詹姆斯", "secrets": "实际已 200 多岁"}],
        "clues": [{"name": "地窖的锁链"}],
        "world_setting": {"era": "现代"},
    }


def _reply(**fields):
    base = {"description": "", "intro": "", "player_brief": "", "changed": []}
    return json.dumps({**base, **fields}, ensure_ascii=False)


def test_泄漏的字段被改写且真相不再出现(monkeypatch):
    parsed = _parsed()
    llm = _LLM(_reply(
        description="调查员寻找失踪的女博士，深入乡间农场",
        intro="现代，美国俄亥俄州乡村。果园与玉米地一望无际，而远离城镇的地方少有人往来。",
        player_brief=parsed["player_brief"],       # 本就没泄漏 → 原样返回
        changed=["description：点破了神话污染", "intro：暗示了地窖"],
    ))
    monkeypatch.setattr(ms, "get_fast_llm", lambda: llm)

    out = asyncio.run(ms.redact_player_facing(parsed, "coc"))
    assert "神话污染" not in out["description"] and "近亲繁殖" not in out["description"]
    assert "地窖" not in out["intro"]
    assert "贝尔博士" in out["player_brief"]        # 没泄漏的那段原样保留
    # 真相与 NPC 秘密、线索名都要喂进审查，否则判不出「什么算泄漏」
    assert "法恩斯沃斯家族" in llm.prompt and "实际已 200 多岁" in llm.prompt
    assert "地窖的锁链" in llm.prompt


def test_world_setting里的两项同步落下(monkeypatch):
    """intro / player_brief 的实际读取处是 world_setting，两边都要落。"""
    parsed = _parsed()
    monkeypatch.setattr(ms, "get_fast_llm", lambda: _LLM(_reply(
        description=parsed["description"], intro="洗过的世界观", player_brief="洗过的钩子",
    )))
    out = asyncio.run(ms.redact_player_facing(parsed, "coc"))
    assert out["world_setting"]["intro"] == "洗过的世界观"
    assert out["world_setting"]["player_brief"] == "洗过的钩子"


def test_把字段删空的产出被弃用(monkeypatch):
    """删到只剩空话等于把字段废掉——宁可留着原来那段（哪怕有泄漏），也不能交一段空的。"""
    parsed = _parsed()
    before = dict(parsed)
    monkeypatch.setattr(ms, "get_fast_llm", lambda: _LLM(_reply()))   # 三段全空
    out = asyncio.run(ms.redact_player_facing(parsed, "coc"))
    assert out["description"] == before["description"]
    assert out["intro"] == before["intro"]


def test_长度暴涨的产出被弃用(monkeypatch):
    """审查只该删减；长度暴涨说明它在自由发挥、多半编了模组里没有的情节。"""
    parsed = _parsed()
    monkeypatch.setattr(ms, "get_fast_llm", lambda: _LLM(_reply(
        description="调查员" + "又" * 200, intro=parsed["intro"], player_brief=parsed["player_brief"],
    )))
    out = asyncio.run(ms.redact_player_facing(parsed, "coc"))
    assert out["description"] == _parsed()["description"]


def test_没有真相时不做审查(monkeypatch):
    """没有真相可比对就无从判断泄漏，白花一次调用。"""
    called = []
    monkeypatch.setattr(ms, "get_fast_llm", lambda: called.append(1))
    parsed = {**_parsed(), "truth": ""}
    out = asyncio.run(ms.redact_player_facing(parsed, "coc"))
    assert out["description"] == _parsed()["description"] and not called


def test_调用失败时原样返回(monkeypatch):
    class _Boom:
        async def complete(self, *a, **k):
            raise RuntimeError("boom")

    monkeypatch.setattr(ms, "get_fast_llm", lambda: _Boom())
    parsed = _parsed()
    out = asyncio.run(ms.redact_player_facing(parsed, "coc"))
    assert out["description"] == _parsed()["description"]


def test_坏JSON时原样返回(monkeypatch):
    monkeypatch.setattr(ms, "get_fast_llm", lambda: _LLM("这不是 JSON"))
    parsed = _parsed()
    out = asyncio.run(ms.redact_player_facing(parsed, "coc"))
    assert out["description"] == _parsed()["description"]


@pytest.mark.parametrize("field", ["description", "intro", "player_brief"])
def test_只改这三个字段其余一律不动(monkeypatch, field):
    parsed = _parsed()
    monkeypatch.setattr(ms, "get_fast_llm", lambda: _LLM(json.dumps({
        field: "洗过的内容",
        "truth": "篡改真相", "npcs": [], "clues": [],     # 越权字段一律无效
    }, ensure_ascii=False)))
    out = asyncio.run(ms.redact_player_facing(parsed, "coc"))
    assert out[field] == "洗过的内容"
    assert out["truth"] == _parsed()["truth"]              # 真相不受影响
    assert out["npcs"] and out["clues"]


# ── 车卡建议的防剧透自检 ──
#
# 与三段玩家可见文本同一个道理，但失败模式更隐蔽：车卡建议从来没被喂过真相与线索，
# 泄漏是**推断**出来的。实测样本：常暗之箱「大量用到侦查、聆听、潜行、逃跑」（等于预告
# 会被追）、分离焦虑「懂遗传学以便理解罕见病线索」、闇暗山「考古学家：剧情场景不涉及古迹」。


class _Module:
    rule_system = "coc"
    title = "常暗之箱"
    description = "深夜末班电车中醒来，乘客消失。"
    world_setting = {"era": "2013", "region": "城市"}
    truth = "奈亚化身把调查员拖进梦境电车，放出怪物追逐他们。"
    npcs = [{"name": "黑衣男子", "secrets": "奈亚拉托提普的化身"}]
    clues = [{"name": "便签"}]


def _guidance():
    return {
        "summary": "适合被困末班电车的普通现代人。",
        "recommended": ["上班族", "大学生"],
        "avoid": ["黑客（电车上没有网络可用）"],
        "notes": ["本子会大量用到侦查、聆听、潜行、逃跑、急救等技能"],
    }


def test_理由从句里的泄漏被删掉(monkeypatch):
    llm = _LLM(json.dumps({
        "summary": "适合被困末班电车的普通现代人。",
        "recommended": ["上班族", "大学生"],
        "avoid": ["黑客（现代都市题材，网络在此类封闭场景用处有限）"],
        "notes": ["侦查、聆听与急救会派上用场"],
        "changed": ["notes：潜行/逃跑的组合预告了会被追"],
    }, ensure_ascii=False))
    monkeypatch.setattr(ms, "get_fast_llm", lambda: llm)

    out = asyncio.run(ms.redact_character_guidance(_guidance(), _Module()))
    assert "逃跑" not in "".join(out["notes"])
    assert out["recommended"] == ["上班族", "大学生"]      # 安全的部分原样保留
    assert "奈亚" in llm.prompt                            # 真相要喂进去当比对面


def test_把建议删废的产出被弃用(monkeypatch):
    """删到只剩空话等于把功能废掉——宁可留着原来那份。"""
    monkeypatch.setattr(ms, "get_fast_llm", lambda: _LLM(json.dumps(
        {"summary": "", "recommended": [], "avoid": [], "notes": []}, ensure_ascii=False)))
    assert asyncio.run(ms.redact_character_guidance(_guidance(), _Module())) == _guidance()


def test_无真相或无建议时不调用(monkeypatch):
    called = []
    monkeypatch.setattr(ms, "get_fast_llm", lambda: called.append(1))

    class _NoTruth(_Module):
        truth = ""

    assert asyncio.run(ms.redact_character_guidance(_guidance(), _NoTruth())) == _guidance()
    assert asyncio.run(ms.redact_character_guidance({}, _Module())) == {}
    assert not called


def test_车卡建议审查失败时原样返回(monkeypatch):
    class _Boom:
        async def complete(self, *a, **k):
            raise RuntimeError("boom")

    monkeypatch.setattr(ms, "get_fast_llm", lambda: _Boom())
    assert asyncio.run(ms.redact_character_guidance(_guidance(), _Module())) == _guidance()


def test_生成函数内部就过审查(monkeypatch):
    """接在生成函数内部，调用方不必各记得加一遍。"""
    seen = {}

    async def fake_redact(guidance, module):
        seen["called"] = True
        return {**guidance, "notes": ["已审查"]}

    monkeypatch.setattr(ms, "get_llm", lambda: _LLM(json.dumps(_guidance(), ensure_ascii=False)))
    monkeypatch.setattr(ms, "redact_character_guidance", fake_redact)
    out = asyncio.run(ms.generate_character_guidance(_Module()))
    assert seen.get("called") and out["notes"] == ["已审查"]
