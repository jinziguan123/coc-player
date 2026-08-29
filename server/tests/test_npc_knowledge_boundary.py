"""NPC 知识边界：KP 握着全局视角，NPC 只该知道自己那一份。

真实事故（鬼屋，房东史蒂芬·诺特）：模组把「罗克斯伯里疗养院」的 description 写成
「精神疗养院，探访疯掉的维托里奥和清醒的加布里埃尔·马卡里奥」——这是给守秘人的笔记。
KP 先让诺特说「哪家医院、孩子们如今在哪儿，我一概不知道」，玩家一追问「这附近有什么
疗养院」，下一轮他就改口成「我托人打听过，维托里奥·马卡里奥先生就关在那里面」。
那句话几乎是场景表 description 的直译：作者底稿被当成了 NPC 的见闻。

根因是 KP 用 ``say`` 代写台词时走的是自己的上下文（含全场景表），而
``build_npc_context`` 那条隔离路径只在 ``npc_act`` 时才走得到。两道防线都只是提示词里
的几句话，最容易在重构里被顺手删掉，这里做确定性回归。

注：``test_kp_context_golden`` 也覆盖了场景表小节的全文，但那是 characterization
快照——下次有人重新生成它时会一并被改掉，拦不住「故意删约束」。这里钉的是意图。
"""

from app.ai.prompts.kp_system import KP_MODULE_DATA_SECTION
from app.ai.tools import TOOLS_BY_NAME


def _scenes_heading() -> str:
    """场景列表小节里、真正的场景 JSON 之前的那段说明。"""
    s = KP_MODULE_DATA_SECTION
    return s[s.index("### 场景列表"):s.index("{scenes_info}")]


def test_场景表标明是作者视角而非角色常识():
    """不标注的话，NPC 会照着 description 的字面把答案念出来。"""
    head = _scenes_heading()
    assert "作者视角" in head
    # 必须点明它约束的是 NPC 的知，而不只是「KP 别剧透」
    assert "NPC" in head
    assert "常识" in head or "知道" in head


def test_场景表说明未抵达场景的描述是给KP的笔记():
    """description 常写成「探访某某」「向某人打听」——那是行动指引，不是角色说得出的话。"""
    head = _scenes_heading()
    assert "description" in head
    assert "底稿" in head or "笔记" in head


def test_say工具限定台词只出自说话人自己知道的事():
    d = TOOLS_BY_NAME["say"].description
    assert "知道" in d
    # 点名底稿的几个来源，否则模型只会理解成「别剧透线索」
    assert "场景列表" in d and "幕后真相" in d


def test_say工具要求同一NPC前后说法自洽():
    """事故的另一半：不是说得太多，是先说不知道、被追问后又知道了。"""
    d = TOOLS_BY_NAME["say"].description
    assert "自洽" in d or "一致" in d
    assert "不知道" in d


def test_say工具不把约束写成一律装傻():
    """反向保护：矫枉过正会让 NPC 见问就推说不知道，线索链直接断掉。

    约束里必须同时交代「本就该知道的事要直说」，否则模型会滑向另一个极端。
    """
    d = TOOLS_BY_NAME["say"].description
    assert "该知道" in d


def test_npc_act走的是隔离上下文这一分工仍在():
    """两条路径的分工是这套防线的前提：say 由 KP 代写（全局视角），
    npc_act 交给 NPC 人格代理（build_npc_context，只有自己那份）。"""
    assert TOOLS_BY_NAME["npc_act"].kind == "npc"
    assert "人格代理" in TOOLS_BY_NAME["npc_act"].description
    # npc_act 早就写明了「知识范围」，say 迟至此次才补上，别再让它掉队
    assert "知识范围" in TOOLS_BY_NAME["npc_act"].description
