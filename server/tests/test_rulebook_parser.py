"""规则书 PDF 结构解析：按字号识别标题层级、组装词条、切子块。

用合成的「行 + 字号」验证组装逻辑（真实 PDF 的整本校验在开发时用三本规则书做过）。
正文 10.6、词条标题 12、节标题 16、章标题 30，与守秘人规则书的排版一致。
"""
from app.services.rulebook_parser import build_sections, embed_text, split_section

BODY, ENTRY, PART, CHAPTER = 10.6, 12.0, 16.0, 30.0


def _filler(n: int) -> list[tuple[str, float]]:
    """凑页数用的正文页（让页眉判定有足够的页数）。"""
    return [(f"第{n}页的正文内容，说明某条规则如何运作。", BODY)]


def _book(chase_page):
    pages = [_filler(i) for i in range(1, 7)]
    for page in pages:
        page.insert(0, ("第七章 追逐", PART))      # 页眉：与章名同字但字号不同，每页都有
        page.append((str(len(pages)), PART))       # 页码
    pages.append(chase_page)
    return pages


CHASE = [
    ("第七章 追逐", CHAPTER),
    ("追逐轮", PART),
    ("与战斗一样，追逐也依照轮次来进行。", BODY),
    ("追逐轮中的行动", ENTRY),
    ("在其依敏捷次序决定的回合中，每", BODY),
    ("一个角色都可以使用其回", BODY),          # 正文折行：短且无句读，旧规则会误当标题
    ("合来：", BODY),
    ("• 在追逐中前进（花费", BODY),
    ("行动点来完成）。", BODY),
    ("• 主动使用格斗、射击", BODY),
    ("或汽车驾驶技能发起", BODY),
    ("一次攻击。", BODY),
    ("• 施放一个法术。", BODY),
    ("在追逐中孤注一掷", ENTRY),
    ("就像战斗一样，在追逐中无法进行孤注一掷。", BODY),
]


def test_按字号识别标题_正文折行不会被误当标题():
    secs = build_sections(_book(CHASE))
    action = next(s for s in secs if s.title == "追逐轮中的行动")
    assert action.path == ["第七章 追逐", "追逐轮", "追逐轮中的行动"]
    assert action.text == (
        "在其依敏捷次序决定的回合中，每一个角色都可以使用其回合来："
        "\n· 在追逐中前进（花费行动点来完成）。"
        "\n· 主动使用格斗、射击或汽车驾驶技能发起一次攻击。"
        "\n· 施放一个法术。"
    )
    assert [s.title for s in secs if s.title][-3:] == ["追逐轮", "追逐轮中的行动", "在追逐中孤注一掷"]


def test_页眉页码被去掉_与页眉同字的章标题仍算标题():
    secs = build_sections(_book(CHASE))
    assert all("第七章 追逐" not in s.text for s in secs)
    assert all(not s.text.rstrip().endswith(("6", "7")) for s in secs)  # 页码没混进正文
    assert next(s for s in secs if s.title == "追逐轮").path[0] == "第七章 追逐"


def test_词条跨页连续并记录每段所在页():
    pages = [
        [("行动点", ENTRY), ("所有角色每一轮都能获得行动点。", BODY)],
        [("行动点被用于从一个地点移至下一个地点。", BODY), ("下一条", ENTRY), ("正文。", BODY)],
    ]
    secs = build_sections(pages)
    points = secs[0]
    assert (points.page_start, points.page_end) == (1, 2)
    assert points.text == "所有角色每一轮都能获得行动点。行动点被用于从一个地点移至下一个地点。"
    assert points.page_at(0) == 1
    assert points.page_at(points.text.index("行动点被用于")) == 2


def test_单字大号字是首字下沉不是标题_同字号连续两行合并成一个标题():
    pages = [[
        ("在", 54.0), ("1920 年代的调查员通常……", BODY),
        ("著名的图书馆", ENTRY), ("和博物馆", ENTRY), ("大英博物馆收藏丰富。", BODY),
    ]]
    secs = build_sections(pages)
    assert secs[0].title == "" and "在" not in [s.title for s in secs]
    assert secs[1].title == "著名的图书馆和博物馆"
    assert secs[1].text == "大英博物馆收藏丰富。"


def test_识别不出标题时一页一个词条_不丢内容():
    pages = [[("第一页正文。", BODY)], [("第二页正文。", BODY)]]
    secs = build_sections(pages)
    assert [(s.page_start, s.text) for s in secs] == [(1, "第一页正文。"), (2, "第二页正文。")]


def test_英文折行补空格_中文直接相连():
    pages = [[("Pushing", ENTRY), ("Pushing the", BODY), ("Roll 孤注", BODY), ("一掷。", BODY)]]
    assert build_sections(pages)[0].text == "Pushing the Roll 孤注一掷。"


def test_子块按句切分_偏移准确_覆盖全文():
    text = "".join(f"第{i}句规则说明，内容大致二十个字左右。" for i in range(30))
    pieces = split_section(text, 200)
    assert len(pieces) > 1
    assert all(len(p) <= 200 + 50 for _at, p in pieces)
    for at, piece in pieces:
        assert text[at:at + len(piece)] == piece
    covered = set()
    for at, piece in pieces:
        covered.update(range(at, at + len(piece)))
    assert covered == set(range(len(text)))


def test_超长单句硬切():
    text = "甲" * 450
    pieces = split_section(text, 200)
    assert [len(p) for _at, p in pieces] == [200, 200, 50]


def test_入向量文本带最后三级路径():
    assert embed_text("第七章 追逐 › 追逐轮 › 追逐轮中的行动", "正文") == "第七章 追逐 › 追逐轮 › 追逐轮中的行动\n正文"
    assert embed_text("甲 › 乙 › 丙 › 丁", "正文") == "乙 › 丙 › 丁\n正文"
    assert embed_text("", "正文") == "正文"


def test_正文字号的整行粗体是最深一级小标题():
    """守秘人规则书的「抵近射击（奖励骰）」与正文同字号同字体，只用伪粗体（填充+描边）标出。"""
    pages = [[
        ("6.5 射击", PART),
        ("射击调整", ENTRY, True),
        ("某些情况会带来奖励骰或惩罚骰。", BODY),
        ("抵近射击（奖励骰）", BODY, True),
        ("如果目标在抵近射击范围内，射击者会获得一个奖励骰。", BODY),
        ("瞄准（奖励骰）", BODY, True),
        ("射击者只有在自己的回合才能声明瞄准。", BODY),
    ]]
    secs = build_sections(pages)
    point = next(s for s in secs if s.title == "抵近射击（奖励骰）")
    assert point.path == ["6.5 射击", "射击调整", "抵近射击（奖励骰）"]
    assert point.text == "如果目标在抵近射击范围内，射击者会获得一个奖励骰。"
    assert next(s for s in secs if s.title == "瞄准（奖励骰）").path[-2] == "射击调整"  # 同级，不嵌套


def test_粗体但带句读的整句不算标题():
    pages = [[("规则", ENTRY), ("注意，这一条非常重要。", BODY, True), ("正文。", BODY)]]
    secs = build_sections(pages)
    assert [s.title for s in secs] == ["规则"]
    assert secs[0].text == "注意，这一条非常重要。正文。"
