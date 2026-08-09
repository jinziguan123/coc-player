"""系统提示分段装配 + prompt caching 的机制测试。

这一层管的是「提示词怎么切、怎么发」，不涉及 KP 叙事质量（那归 evals）。
"""

import json
import logging

import pytest

from app.ai import context as ctx
from app.ai.provider import CACHE_BLOCKS_KEY, strip_provider_keys
from app.ai.providers.anthropic import AnthropicProvider


# ── 分段装配 ────────────────────────────────────────────────────────────


def _seg(text, tier, priority, label):
    return ctx._Seg(text, tier, priority, label)


def test_按_tier_分组_静态在前():
    """块顺序必须是 静态 → 半静态 → 易变：缓存是前缀匹配，顺序反了就永远命中不了。"""
    blocks, _ = ctx._assemble_system([
        _seg("易变", ctx.SYS_TIER_VOLATILE, 10, "v"),
        _seg("静态", ctx.SYS_TIER_STATIC, 0, "s"),
        _seg("半静态", ctx.SYS_TIER_SEMI, 0, "m"),
    ], max_tokens=10_000)
    assert blocks == ["静态", "半静态", "易变"]


def test_同_tier_内保持入列顺序():
    blocks, _ = ctx._assemble_system([
        _seg("甲", ctx.SYS_TIER_STATIC, 0, "a"),
        _seg("乙", ctx.SYS_TIER_STATIC, 0, "b"),
    ], max_tokens=10_000)
    assert blocks[0] == "甲乙"


def test_超预算按_priority_从大到小整段丢():
    segs = [
        _seg("必留" * 200, ctx.SYS_TIER_STATIC, 0, "keep"),
        _seg("先丢" * 200, ctx.SYS_TIER_VOLATILE, 40, "drop-first"),
        _seg("后丢" * 200, ctx.SYS_TIER_VOLATILE, 5, "drop-later"),
    ]
    full = sum(ctx._estimate_tokens(s.text) for s in segs)
    # 预算只够两段：priority 最大的那段应当被丢掉，低 priority 的留下
    blocks, total = ctx._assemble_system(segs, max_tokens=int(full * 0.7))
    joined = "".join(blocks)
    assert "先丢" not in joined
    assert "后丢" in joined and "必留" in joined
    assert total <= int(full * 0.7)


def test_丢段日志说清超了多少与能否取回(caplog):
    """只报个段名的话，读日志的人无从判断这是正常代谢还是预算该调了。

    摘录类的段是按需可取回的（KP 手上留着 [MODULE_LOOKUP]/[RULE_LOOKUP]），
    这也正是它们排在丢弃序列最前面的原因——日志得把这层说出来，
    否则每回合一条「丢弃段落：module-excerpts」看着像在掉东西。
    """
    segs = [
        _seg("必留" * 200, ctx.SYS_TIER_STATIC, 0, "keep"),
        _seg("摘录" * 200, ctx.SYS_TIER_VOLATILE, 40, "module-excerpts"),
    ]
    full = sum(ctx._estimate_tokens(s.text) for s in segs)
    with caplog.at_level(logging.WARNING):
        ctx._assemble_system(segs, max_tokens=int(full * 0.7))
    assert "module-excerpts" in caplog.text
    assert str(full) in caplog.text                 # 超之前的总量
    assert "MODULE_LOOKUP" in caplog.text           # 可按需取回

    # 丢的不是摘录类时，不该给出「可取回」的暗示
    caplog.clear()
    segs = [
        _seg("必留" * 200, ctx.SYS_TIER_STATIC, 0, "keep"),
        _seg("台账" * 200, ctx.SYS_TIER_VOLATILE, 5, "clue-ledger"),
    ]
    with caplog.at_level(logging.WARNING):
        ctx._assemble_system(segs, max_tokens=int(full * 0.7))
    assert "clue-ledger" in caplog.text
    assert "MODULE_LOOKUP" not in caplog.text


def test_priority_0_的段永不丢弃():
    """宁可超预算也不切碎裁定手册/模组 JSON——截出半截非法 JSON 比超预算糟得多。"""
    segs = [_seg("必留" * 500, ctx.SYS_TIER_STATIC, 0, "keep")]
    blocks, total = ctx._assemble_system(segs, max_tokens=10)
    assert "".join(blocks) == "必留" * 500
    assert total > 10  # 如实反映超了，不假装没超


def test_丢弃不切碎_JSON():
    """回归：旧实现从尾部截字符，会把紧凑 JSON 截成非法串喂给模型。"""
    payload = json.dumps([{"id": f"npc{i}", "name": "某人"} for i in range(60)],
                         ensure_ascii=False, separators=(",", ":"))
    blocks, _ = ctx._assemble_system([
        _seg("静态" * 100, ctx.SYS_TIER_STATIC, 0, "keep"),
        _seg(payload, ctx.SYS_TIER_SEMI, 0, "module-data"),
    ], max_tokens=50)
    body = blocks[1]
    assert body == "" or json.loads(body)  # 要么整段没了，要么仍是合法 JSON


# ── Anthropic：system blocks 与缓存断点 ──────────────────────────────────


@pytest.fixture
def provider():
    return AnthropicProvider(model="claude-opus-5", api_key="test")


def test_只抽开头连续的_system_中间的留原位(provider):
    """回归：旧实现把每条 system 都 join 到顶部，上下文层的插入位置全部失效。"""
    blocks, rest = provider._split_system([
        {"role": "system", "content": "系统提示"},
        {"role": "user", "content": "玩家发言"},
        {"role": "system", "content": "[格式提醒] 台词要带引号"},
        {"role": "user", "content": "再一句"},
    ])
    assert len(blocks) == 1 and blocks[0]["text"] == "系统提示"
    # 中间那条不能跑到系统提示里去，且必须还在它原来的位置上
    assert [m["role"] for m in rest] == ["user", "user", "user"]
    assert rest[1]["content"] == "[格式提醒] 台词要带引号"


def test_缓存断点只打前两块(provider):
    blocks, _ = provider._split_system([{
        "role": "system",
        "content": "静态半静态易变",
        CACHE_BLOCKS_KEY: ["静态", "半静态", "易变"],
    }])
    assert [b["text"] for b in blocks] == ["静态", "半静态", "易变"]
    assert blocks[0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert blocks[1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    # 第三块每轮都变，打了断点也永远不会命中，只会白占一个断点额度（上限 4）
    assert "cache_control" not in blocks[2]


def test_无分块键时回落单块(provider):
    """没挂 CACHE_BLOCKS_KEY 的调用方（NPC 上下文、生图提示词）行为不变。"""
    blocks, _ = provider._split_system([{"role": "system", "content": "单块"}])
    assert len(blocks) == 1 and blocks[0]["text"] == "单块"


def test_无_system_时返回_None(provider):
    blocks, rest = provider._split_system([{"role": "user", "content": "你好"}])
    assert blocks is None and len(rest) == 1


def test_分块里的空串被丢掉(provider):
    """三档里某档为空（如开场没有台账）不应产生空 text 块——Anthropic 会拒绝。"""
    blocks, _ = provider._split_system([{
        "role": "system", "content": "甲乙", CACHE_BLOCKS_KEY: ["甲", "", "乙"],
    }])
    assert [b["text"] for b in blocks] == ["甲", "乙"]


# ── 进程内元数据不得泄漏到请求体 ────────────────────────────────────────


def test_strip_provider_keys_剔除元数据():
    msgs = [
        {"role": "system", "content": "x", CACHE_BLOCKS_KEY: ["x"]},
        {"role": "user", "content": "y"},
    ]
    out = strip_provider_keys(msgs)
    assert CACHE_BLOCKS_KEY not in out[0]
    assert out[0]["content"] == "x" and out[1] is msgs[1]  # 无该键的消息不复制
    assert CACHE_BLOCKS_KEY in msgs[0]                     # 不就地修改调用方的数据


def test_元数据可被_json_序列化剔除后发出():
    """OpenAI 兼容端点收到未知字段会 400，这条守住那个边界。"""
    msgs = strip_provider_keys([
        {"role": "system", "content": "x", CACHE_BLOCKS_KEY: ["x"]},
    ])
    assert CACHE_BLOCKS_KEY not in json.dumps(msgs)


# ── usage 归一 ──────────────────────────────────────────────────────────


def test_usage_把缓存读写并入输入总量(provider):
    """Anthropic 的 input_tokens 只含未命中部分，下游要的是真实输入总量。"""
    provider._set_usage({
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_input_tokens": 900,
        "cache_creation_input_tokens": 0,
    })
    u = provider.last_usage
    assert u["prompt_tokens"] == 1000        # 100 + 900，不是 100
    assert u["cache_read_input_tokens"] == 900
    assert u["total_tokens"] == 1050


def test_无缓存字段时行为不变(provider):
    provider._set_usage({"input_tokens": 100, "output_tokens": 50})
    u = provider.last_usage
    assert u["prompt_tokens"] == 100 and u["cache_read_input_tokens"] == 0


# ── 估算口径的在线校准 ──────────────────────────────────────────────────


def test_未校准时系数为_1():
    """没有实测样本的会话行为与校准前完全一致。"""
    assert ctx.budget_scale({}) == 1.0
    assert ctx.budget_scale(None) == 1.0
    assert ctx.effective_context_budget({}, 48_000) == 48_000


def test_首次校准直接采用比值():
    """估算 1.5 万、实测 1 万 → 估算高估 1.5 倍，预算该放大 1.5 倍。"""
    ws = ctx.update_budget_scale({}, measured=10_000, estimated=15_000)
    assert ws[ctx.CALIBRATION_KEY] == 1.5
    assert ctx.effective_context_budget(ws, 48_000) == 72_000


def test_后续校准走_EMA_不被单轮带偏():
    ws = ctx.update_budget_scale({}, measured=10_000, estimated=15_000)   # 1.5
    ws = ctx.update_budget_scale(ws, measured=10_000, estimated=10_000)   # 本轮 1.0
    # EMA(α=0.3)：1.5×0.7 + 1.0×0.3 = 1.35，没有被一轮拉到 1.0
    assert ws[ctx.CALIBRATION_KEY] == 1.35


def test_校准系数被夹在安全区间内():
    high = ctx.update_budget_scale({}, measured=1, estimated=10_000)
    assert high[ctx.CALIBRATION_KEY] == ctx.CALIBRATION_MAX
    low = ctx.update_budget_scale({}, measured=10_000, estimated=1)
    assert low[ctx.CALIBRATION_KEY] == ctx.CALIBRATION_MIN


def test_坏样本不改动系数():
    """一次坏数据不能把预算带飞——宁可继续用旧系数。"""
    ws = ctx.update_budget_scale({}, measured=10_000, estimated=15_000)
    assert ctx.update_budget_scale(ws, measured=0, estimated=15_000) == ws
    assert ctx.update_budget_scale(ws, measured=10_000, estimated=0) == ws


def test_校准是纯函数_不就地改():
    ws = {"other": 1}
    out = ctx.update_budget_scale(ws, measured=10_000, estimated=12_000)
    assert ctx.CALIBRATION_KEY not in ws and out["other"] == 1


def test_低估时收缩预算():
    """估算低于实测说明启发式在低估，此时必须缩预算，否则真的溢出窗口。"""
    ws = ctx.update_budget_scale({}, measured=20_000, estimated=16_000)
    assert ws[ctx.CALIBRATION_KEY] == 0.8
    assert ctx.effective_context_budget(ws, 48_000) == 38_400


# ── 空产出的根因诊断 ────────────────────────────────────────────────────


def _compat_provider():
    from app.ai.providers.openai_compat import OpenAICompatProvider
    return OpenAICompatProvider(model="deepseek-v4-flash", base_url="http://x", api_key="k")


def test_空产出且有思考时指向关思考开关(caplog):
    """complete() 只收 delta.content，模型把话全说进思考里就会返回空串——
    上层只看到「输出无法解析」，根因在这一层却完全静默过。"""
    p = _compat_provider()
    p.last_usage = {
        "completion_tokens": 1200,
        "completion_tokens_details": {"reasoning_tokens": 1200},
    }
    with caplog.at_level("WARNING"):
        p._warn_empty_completion()
    msg = caplog.text
    assert "思考" in msg and "关闭模型思考" in msg
    assert "deepseek-v4-flash" in msg          # 带上模型名，多档配置下才知道是哪个


def test_空产出且无思考时给中性提示(caplog):
    p = _compat_provider()
    p.last_usage = {"completion_tokens": 0}
    with caplog.at_level("WARNING"):
        p._warn_empty_completion()
    assert "供应商侧异常" in caplog.text
    assert "关闭模型思考" not in caplog.text   # 不是这个原因就别乱指方向


def test_无_usage_时不炸(caplog):
    p = _compat_provider()
    p.last_usage = None
    with caplog.at_level("WARNING"):
        p._warn_empty_completion()             # 诊断日志绝不能自己成为故障源
    assert caplog.text
