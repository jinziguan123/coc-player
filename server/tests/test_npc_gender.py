"""NPC 性别必须在解析阶段定下来，别让 KP 靠名字猜。

实测事故：整份《鬼屋》模组的 gender 全空，KP 把马卡里奥家的姐妹一路叙述成了兄弟。
两处成因——首轮 prompt 把判据写成了「外观辨不出就留空」（可「维托里奥的妻子」是身份
关系，不是外观），质检员的重点排查清单里也没有这一项。

**为什么不做确定性的关键词兜底**：试过，在真实模组上错误率约 1/3。代词和亲属词经常指
别人——「其干儿子刘祥照料日常」（黄婆被判成 male）、「曾目击他夹着书走向公墓」（莱拉
被判成 male）；怪物的外形描写也会命中——「体型庞大的男子…生有巨大金狼头」。
判性别要读完整原文和上下文，那是解析器的活，不是几十字摘要上的词表能干的。
"""

from app.services.module_service import PARSE_PROMPT_TEMPLATE, SUPPLEMENT_PROMPT_TEMPLATE


def _gender_line(template: str) -> str:
    return next(ln for ln in template.splitlines() if "gender" in ln)


def test_首轮按称呼与身份关系判断而不是按外观():
    """原措辞「外观辨不出性别就留空」把判据引向了长相，于是整份模组全留空。"""
    line = _gender_line(PARSE_PROMPT_TEMPLATE)
    assert "称呼" in line or "身份关系" in line
    assert "妻子" in line                      # 给具体对照，不能只说「判断一下」
    assert "外文译名" in line or "别按" in line


def test_质检员会逐个核对性别():
    """首轮漏填是常态，质检读的是原文，正是补这一项的地方。"""
    assert "gender" in SUPPLEMENT_PROMPT_TEMPLATE
    assert "逐个核对" in SUPPLEMENT_PROMPT_TEMPLATE


def test_质检员被提醒当心指代():
    """踩过的坑要写进 prompt：亲属词与代词常常指的是别人，不是这个 NPC 本人。"""
    tpl = SUPPLEMENT_PROMPT_TEMPLATE
    assert "指代" in tpl
    assert "非人" in tpl and "群体" in tpl      # 这两类不该被硬填性别
    assert "留空" in tpl


def test_两份prompt都警告别按名字猜():
    """外文译名在中文里看不出性别——这正是加布里埃尔·马卡里奥被写成男人的原因。"""
    for tpl in (PARSE_PROMPT_TEMPLATE, SUPPLEMENT_PROMPT_TEMPLATE):
        assert "加布里埃尔" in tpl or "外文译名" in tpl
