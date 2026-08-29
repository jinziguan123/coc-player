"""``filter_narration_stream`` 的行为语料：金标准测试与重构对照共用。

每条 = (用例名, 输入文本, kwargs)。用例名即断言失败时的定位线索，务必写清它测的是哪条分支。
输入按整串给出；分词方式由测试侧决定（见 test_narration_protocol_golden 的分词不变性）。
"""

from __future__ import annotations

from app.services.narration_protocol import filter_narration_stream

NPCS = [
    {"name": "史蒂芬·诺特"},
    {"name": "霍尔护士长"},
    {"name": "玛莎"},
    {"name": "调查员阿宁", "is_player": True},
]

PARTY = {"调查员阿宁"}

# (name, text, kwargs)
CASES: list[tuple[str, str, dict]] = [
    # ── 纯旁白 ────────────────────────────────────────────────
    ("纯旁白", "雨水顺着屋檐落下，街角一片死寂。", {}),
    ("段落分隔按段提交", "第一段旁白到此。\n\n第二段旁白开始。", {}),
    (
        "超长旁白按句边界切分",
        "走廊很长。" * 40,
        {},
    ),

    # ── 显式 [SAY] ────────────────────────────────────────────
    ("SAY 显式说话人", "他抬起头。[SAY: who=玛莎]你们终于来了。[/SAY]然后又低下头。", {"npcs": NPCS}),
    ("SAY 内层引号被剥掉", "[SAY: who=玛莎]“别过去。”[/SAY]", {"npcs": NPCS}),
    ("SAY 未闭合遇空行即收束", "[SAY: who=玛莎]我什么都没看见。\n\n她转身走了。", {"npcs": NPCS}),
    ("SAY 未闭合流结束时收束", "[SAY: who=玛莎]别problem过来。", {"npcs": NPCS}),
    ("SAY 归到玩家党则丢弃气泡", "[SAY: who=调查员阿宁]我来开门。[/SAY]", {"npcs": NPCS, "party_names": PARTY}),
    ("SAY 名字走别名归一", "[SAY: who=诺特]账本不在我这儿。[/SAY]", {"npcs": NPCS}),

    # ── 裸引号：说话人判定 ────────────────────────────────────
    ("显式前缀说话人并抹掉前缀", "玛莎说道：“东西在阁楼上。”", {"npcs": NPCS}),
    ("承接上一位说话人", "玛莎说道：“东西在阁楼上。”她顿了顿。“别让他看见。”", {"npcs": NPCS}),
    ("段落分隔释放承接", "玛莎说道：“东西在阁楼上。”\n\n报纸上写着“本地男子失踪”。", {"npcs": NPCS}),
    ("最近主语弱推断", "霍尔护士长走进病房。“该换药了。”", {"npcs": NPCS}),
    ("两个 NPC 主语则不猜", "玛莎和霍尔护士长站在门口。“谁先说？”", {"npcs": NPCS}),
    ("书写标识留旁白", "门牌上写着“303”。", {"npcs": NPCS}),
    ("相邻书写串整串留旁白", "门牌依次写着“301”、“302”、“303”。", {"npcs": NPCS}),
    ("被谈论者不作说话人", "玛莎压低声音。“史蒂芬·诺特藏得很深。”", {"npcs": NPCS}),
    ("玩家党不被代言", "调查员阿宁走上前。“交给我。”", {"npcs": NPCS, "party_names": PARTY}),
    ("弱信号下短标签不算台词", "霍尔护士长指着牌子。“禁入”", {"npcs": NPCS}),
    ("所有格后的名字不作说话人", "史蒂芬·诺特的遗嘱执行人在场。“请节哀。”", {"npcs": NPCS}),

    # ── 后置说话人（deferring）────────────────────────────────
    ("后置说话人抽成气泡", "房间里很暗。“你们不该来的。”她低声说。", {"npcs": NPCS}),
    ("后置具名说话人", "房间里很暗。“你们不该来的，”霍尔护士长说。", {"npcs": NPCS}),
    ("后置未等到动词则归还旁白", "房间里很暗。“你们不该来的。”窗外传来钟声，很久。", {"npcs": NPCS}),
    ("后置遇换行归还旁白", "房间里很暗。“你们不该来的。”\n窗外传来钟声。", {"npcs": NPCS}),
    ("流结束时仍在等待则归还", "房间里很暗。“你们不该来的。”", {"npcs": NPCS}),

    # ── 括号标签 ──────────────────────────────────────────────
    ("MOVE 标签内联剔除", "他走向门口。[MOVE: to=走廊]门开了。", {}),
    ("MAP_MARK 标签内联剔除", "他做了记号。[MAP_MARK: id=x]继续前行。", {}),
    ("GROUP 标签剥掉且不采纳分组", "[GROUP: scene=诺特的事务所]两人推开门。", {}),
    ("全角括号等价", "他走向门口。【MOVE: to=走廊】门开了。", {}),
    ("认不出的括号一律丢弃", "旁白开始。[隐藏检定：未知]旁白继续。", {}),
    ("命令标签终止本次流", "他伸手去够抽屉。[DICE_CHECK: skill=侦查]后面不该出现。", {}),
    ("流结束时未闭合括号归还", "旁白开始。[未闭合", {}),

    # ── 开关与去重 ────────────────────────────────────────────
    ("关闭猜测则裸引号全留旁白", "霍尔护士长走进病房。“该换药了。”", {"npcs": NPCS, "guess_speakers": False}),
    ("分组标签确定性注入", "两人推开门。", {"group_label": "街区"}),
    (
        "已展示台词整段丢弃",
        "玛莎说道：“东西在阁楼上。”",
        {"npcs": NPCS, "shown_dialogues": ["东西在阁楼上。"]},
    ),
    (
        "与既有旁白重复的段落不重复提交",
        "月光照在积水的石阶上，远处传来断续的犬吠声，谁也没有先开口。",
        {"prior_narration": "月光照在积水的石阶上，远处传来断续的犬吠声，谁也没有先开口。"},
    ),
    ("流结束时未闭合引号留旁白", "他开口道：“我其实", {"npcs": NPCS}),
]


async def run(text: str, kwargs: dict, chunk: int = 3) -> dict:
    """按 chunk 字切分喂进过滤器，收集流式产物与落库结果。chunk=0 表示整串一次给完。"""

    async def gen():
        if chunk == 0:
            yield text
            return
        for i in range(0, len(text), chunk):
            yield text[i:i + chunk]

    result = ["", "", [], [], []]
    chunks = []
    async for c in filter_narration_stream(gen(), result, **kwargs):
        d = c.model_dump()
        chunks.append([
            d["type"], d.get("content") or "", d.get("actor_name"),
            (d.get("metadata") or {}).get("group"),
        ])
    return {
        "chunks": chunks,
        "narration": result[0],
        "full": result[1],
        "extracted": [list(x) for x in result[2]],
        "marks": [list(x) for x in result[3]],
        "groups": [list(x) for x in result[4]],
    }
