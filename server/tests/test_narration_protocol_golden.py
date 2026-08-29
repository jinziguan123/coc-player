"""``filter_narration_stream`` 的金标准（characterization）测试。

**这组测试的性质与别处不同**：它断言的不是「什么才对」，而是「当前实现就是这样」。
存在的理由是 ``filter_narration_stream`` 是一台字符级状态机（引号/方括号/[SAY]/后置
说话人四个状态相互嵌套），改它的人需要一张能立刻告诉他「你碰到了哪条分支」的网，
而不是跑完整条聊天链路去猜哪里变了。

两层保障：

1. **快照比对**（``fixtures/narration_golden.json``）——逐用例钉死流式 chunk 序列与
   落库产物（旁白、气泡、对话位点、分组）。
2. **分词不变性**——同一段文本按 1/2/3/5/13 字与整串喂进去，结果必须一致。
   这是流式解析器最容易被改坏的性质：任何「攒够一个 token 再判断」的写法都会在
   模型换一种切分方式时行为漂移，而线上 Provider 的切分是不可控的。
   （口径：忽略纯空白的 chunk 切分差异——空白段是否单独广播本就随分词浮动，
   落库产物 ``result[0..4]`` 则必须逐字节相同。）

**快照怎么更新**：只有在**有意**改变行为时才更新，且必须在提交信息里说明改了哪几条。
重新生成：``.venv/bin/python -m tests.regen_narration_golden``（见该模块）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests._narration_corpus import CASES, run as _run

GOLDEN = json.loads(
    (Path(__file__).parent / "fixtures" / "narration_golden.json").read_text("utf-8")
)




def _normalize(run: dict) -> tuple:
    """分词不变式的比较口径：相邻同类 chunk 合并、丢掉纯空白，落库产物逐字节比。"""
    merged: list[list] = []
    for kind, content, actor, group in run["chunks"]:
        if merged and merged[-1][0] == kind and merged[-1][2] == actor and merged[-1][3] == group:
            merged[-1][1] += content
        else:
            merged.append([kind, content, actor, group])
    shaped = [(k, c.strip(), a, g) for k, c, a, g in merged if c.strip()]
    # 刻意不比 full_response：它是**已知**随分词浮动的（见下方 test_命令标签终止时…）。
    return (
        shaped, run["narration"],
        tuple(map(tuple, run["extracted"])),
        tuple(map(tuple, run["marks"])),
        tuple(map(tuple, run["groups"])),
    )


@pytest.mark.parametrize("name,text,kwargs", CASES, ids=[c[0] for c in CASES])
@pytest.mark.asyncio
async def test_行为与金标准一致(name, text, kwargs):
    assert name in GOLDEN, f"语料新增了用例 {name!r}，请重新生成金标准快照"
    assert await _run(text, kwargs) == GOLDEN[name]


@pytest.mark.parametrize("name,text,kwargs", CASES, ids=[c[0] for c in CASES])
@pytest.mark.asyncio
async def test_结果与分词方式无关(name, text, kwargs):
    """Provider 怎么切 token 不该改变产物——这是流式解析器最易被改坏的性质。"""
    ref = _normalize(await _run(text, kwargs, chunk=3))
    for size in (1, 2, 5, 13, 0):
        got = _normalize(await _run(text, kwargs, chunk=size))
        assert got == ref, f"{name}: 按 {size or '整串'} 字切分时结果漂移"


@pytest.mark.asyncio
async def test_金标准覆盖了全部语料():
    """防止语料被删条却没人发现（快照里多出的孤儿键同样要暴露）。"""
    assert set(GOLDEN) == {c[0] for c in CASES}


@pytest.mark.asyncio
async def test_命令标签终止时_full_response_随分词浮动_已知缺口():
    """**记录一处既有缺口，不是期望行为。**

    命令标签（[DICE_CHECK] 等）终止本次流时，``full_response`` 已经把「标签所在的
    那个 token」整体收进去了——``full_response += token`` 发生在 token 粒度，而
    ``break`` 只跳出字符循环。于是标签之后、同一 token 之内的文字会残留在
    ``full_response`` 里，残留多少取决于 Provider 怎么切词（不可控）。

    这有实际影响：``full_response`` 正是下游 ``_process_commands`` 解析指令的输入，
    紧跟在终止标签后的第二条指令**可能被解析、也可能不被**，取决于分词。

    此处只把现状钉住：改这条要连同 ``kp_tool_loop`` 的指令解析一起评估，
    不该在重构里顺手改掉。旁白与气泡（result[0]/[2]/[3]/[4]）不受影响。
    """
    text = "他伸手去够抽屉。[DICE_CHECK: skill=侦查]后面不该出现。"
    tail = "后面不该出现。"

    fine = await _run(text, {}, chunk=3)      # 细切：标签闭合处正好断开
    coarse = await _run(text, {}, chunk=0)    # 整串：标签与后文同在一个 token

    assert not fine["full"].endswith(tail)
    assert coarse["full"].endswith(tail)
    # 面向玩家的产物两边一致——缺口只在 full_response 这一层
    assert fine["narration"] == coarse["narration"] == "他伸手去够抽屉。"
    assert fine["extracted"] == coarse["extracted"] == []
