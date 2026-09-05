"""事后去口癖编辑器：落库前把带口癖的句子改成直陈，改不好就保留原文。"""

import asyncio

from app.services import style_editor
from app.services.turn_context import _validate_and_patch_narration

# 鬼屋一局第 156 段：一处「像是」揣测 + 一处破折号补充
SHOT = ("加布里埃尔的手指停在针线上，没有立刻抬起来。"
        "她低着头，看着膝上那叠碎布包，指尖沿着布边按了一按，像是检查针脚有没有散。"
        "片刻后她把针线活整份搁到藤椅扶手上，抬起脸来，目光在陈守一脸上定了定，又移向门口那几位——尤其是坂田桐时。"
        "她没有急于回答，先把那句问话在嘴里过了一遍，才开口。")
CLEAN = ("加布里埃尔的手指停在针线上，没有立刻抬起来。"
         "她低着头，看着膝上那叠碎布包，指尖沿着布边按了一按，针脚没有散。"
         "片刻后她把针线活整份搁到藤椅扶手上，抬起脸来，目光在陈守一脸上定了定，又移向门口那几位，在坂田桐时脸上多停了一下。"
         "她没有急于回答，先把那句问话在嘴里过了一遍，才开口。")


class _FakeLLM:
    def __init__(self, reply=CLEAN, raise_exc=None):
        self.reply, self.raise_exc, self.calls = reply, raise_exc, []

    async def complete(self, messages, temperature=0.7, max_tokens=None, response_format=None):
        self.calls.append(messages)
        if self.raise_exc:
            raise self.raise_exc
        return self.reply


class _FakeAgent:
    def __init__(self, llm):
        self.llm = llm


def test_挑出带口癖的句子():
    got = style_editor.flagged_sentences(SHOT)
    assert len(got) == 2
    assert "像是检查针脚" in got[0] and "——尤其是" in got[1]
    assert style_editor.flagged_sentences(CLEAN) == []


def test_没口癖就不调用模型():
    llm = _FakeLLM()
    assert asyncio.run(style_editor.polish_narration(llm, CLEAN)) is None
    assert llm.calls == []


def test_改写通过校验就替换_并重映射交错偏移():
    llm = _FakeLLM()
    off = len("加布里埃尔的手指停在针线上，没有立刻抬起来。")
    result = [SHOT, SHOT, [("加布里埃尔", "先坐。")], [(off, "加布里埃尔", "先坐。")], []]
    assert asyncio.run(style_editor.polish_result(llm, result)) is True
    assert result[0] == CLEAN
    assert result[1] == SHOT                       # 供指令解析的原文不动
    new_off = result[3][0][0]
    assert 0 < new_off <= len(CLEAN) and CLEAN[new_off - 1] in "。！？"   # 吸附到句界
    # 把标出的句子交给了模型，且编辑器提示里自己不带破折号示范
    user_msg = llm.calls[0][-1]["content"]
    assert "像是检查针脚有没有散" in user_msg and "——" not in llm.calls[0][0]["content"]


def test_改完口癖没减少就保留原文():
    still_bad = SHOT.replace("像是检查针脚有没有散", "仿佛在检查针脚有没有散")
    llm = _FakeLLM(reply=still_bad)
    assert asyncio.run(style_editor.polish_narration(llm, SHOT)) is None


def test_长度失真就保留原文():
    assert asyncio.run(style_editor.polish_narration(_FakeLLM(reply="她抬起头。"), SHOT)) is None
    assert asyncio.run(style_editor.polish_narration(_FakeLLM(reply=CLEAN * 3), SHOT)) is None


def test_模型报错保留原文_不抛():
    result = [SHOT, SHOT, [], [], []]
    assert asyncio.run(style_editor.polish_result(_FakeLLM(raise_exc=RuntimeError("boom")), result)) is False
    assert result[0] == SHOT


def test_接受包着_provider_的_agent():
    result = [SHOT, SHOT, [], [], []]
    assert asyncio.run(style_editor.polish_result(_FakeAgent(_FakeLLM()), result)) is True
    assert result[0] == CLEAN


def test_剥掉代码栏与前缀():
    assert style_editor._clean_output("```\n改写后：" + CLEAN + "\n```") == CLEAN


def test_主路径没有裁定计划也要过编辑器():
    """校验器依赖 plan，编辑器不依赖：plan 为空的轮次（旁路续写等）同样要去口癖。"""
    llm = _FakeLLM()
    result = [SHOT, SHOT, [], [], []]
    asyncio.run(_validate_and_patch_narration(llm, None, result))
    assert result[0] == CLEAN and len(llm.calls) == 1
