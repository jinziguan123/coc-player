"""易变档挪到历史尾部（settings.kp_volatile_tail）的机制测试。

要守住的只有两件事：
1. **内容不变，只换位置**：开关开/关，KP 看到的每一个字都一样，只是易变档从系统提示末尾
   挪进了历史尾部的「当前局面」快照；
2. **前缀稳定**：同一会话连续两轮、只有易变内容和新事件变化时，快照之前的消息逐字一致
   ——这正是 DeepSeek / OpenAI 前缀缓存能命中整段剧情历史的前提。
"""

from __future__ import annotations

import pytest

from app.ai import context as ctx
from app.ai.provider import CACHE_BLOCKS_KEY, CACHE_BREAK_KEY, strip_provider_keys
from app.ai.providers.anthropic import AnthropicProvider
from app.config import settings
from app.models import EventLog
from app.services.turn_context import _cache_usage
from tests._kp_context_corpus import CASES, _char, _events, _mates, _module, _session


@pytest.fixture
def tail(monkeypatch):
    monkeypatch.setattr(settings, "kp_volatile_tail", True)


def _build(monkeypatch, on: bool, kwargs: dict) -> list[dict]:
    monkeypatch.setattr(settings, "kp_volatile_tail", on)
    return ctx.build_kp_context(**kwargs)


def _snapshot_index(msgs: list[dict]) -> int:
    idx = [i for i, m in enumerate(msgs) if ctx._is_kp_snapshot(m)]
    assert len(idx) == 1, "每轮应恰好一条当前局面快照"
    return idx[0]


# ── 内容不变，只换位置 ──────────────────────────────────────────────


@pytest.mark.parametrize("name,kwargs", CASES, ids=[c[0] for c in CASES])
def test_开关只改位置不改内容(name, kwargs, monkeypatch):
    off = _build(monkeypatch, False, kwargs)
    on = _build(monkeypatch, True, kwargs)

    snaps = [m for m in on if ctx._is_kp_snapshot(m)]
    volatile = snaps[0]["content"].removeprefix(ctx._KP_SNAPSHOT_HEAD) if snaps else ""
    # 系统提示 = 前两档；补上快照里的易变档就是旧装配的完整系统提示
    assert on[0]["content"] + volatile == off[0]["content"]
    head = on[0][CACHE_BLOCKS_KEY]
    assert "".join(head) == on[0]["content"]
    assert head == off[0][CACHE_BLOCKS_KEY][:len(head)]       # 缓存分块就是旧分块去掉易变块
    # 其余消息（历史、摘要、交接提示、格式提醒、文风纠偏）一条不差、顺序不变
    rest_on = [m for m in strip_provider_keys(on[1:]) if not ctx._is_kp_snapshot(m)]
    assert rest_on == off[1:]


def test_开关关闭时没有快照也不标断点(monkeypatch):
    msgs = _build(monkeypatch, False, dict(CASES)["线索台账全量渲染"])
    assert not any(ctx._is_kp_snapshot(m) for m in msgs)
    assert not any(CACHE_BREAK_KEY in m for m in msgs)
    assert "线索台账" in msgs[0]["content"]


def test_快照在回合交接与玩家消息之前(tail):
    kw = dict(dict(CASES)["线索台账全量渲染"])
    kw["events"] = [*_events(), _player_action(4, "我去当铺看看。")]
    msgs = ctx.build_kp_context(**kw)
    i = _snapshot_index(msgs)
    assert "线索台账" in msgs[i]["content"]
    assert "线索台账" not in msgs[0]["content"]
    assert msgs[-1]["role"] == "user" and "我去当铺看看" in msgs[-1]["content"]
    # 快照之后只剩本轮的临场提示与玩家消息：不夹任何历史
    assert all(m["role"] == "system" for m in msgs[i + 1:-1])
    # 稳定前缀的终点标在快照前一条历史消息上
    assert msgs[i - 1].get(CACHE_BREAK_KEY) is True
    # system_text 仍能找到挪走的内容（测试与用量估算的口径不变）
    assert "线索台账" in ctx.system_text(msgs)


def test_开场时快照在开场指令之前(tail):
    msgs = ctx.build_kp_context(**dict(CASES)["封路清单"] | {"events": []})
    assert msgs[-1]["role"] == "user"
    i = _snapshot_index(msgs)
    assert i == 1                                   # 紧跟系统提示
    assert CACHE_BREAK_KEY not in msgs[0]           # 系统提示自带断点，不重复标


# ── 前缀稳定 ────────────────────────────────────────────────────────


def _player_action(seq: int, text: str) -> EventLog:
    return EventLog(session_id="sess-1", sequence_num=seq, event_type="action",
                    actor_id="c-陈守一", actor_name="陈守一", content=text)


def _turn(events: list[EventLog], ws_extra: dict | None) -> dict:
    return dict(session=_session(world_state_extra=ws_extra), module=_module(),
                player_char=_char("陈守一"), events=events, teammates=_mates())


def _two_turns() -> tuple[dict, dict]:
    turn1 = [*_events(), _player_action(4, "我去当铺看看。")]
    turn2 = [
        *turn1,
        EventLog(session_id="sess-1", sequence_num=5, event_type="narration",
                 actor_name="KP", content="当铺老板抬起头，把一封信压在掌心下。"),
        _player_action(6, "我把委托书推过去。"),
    ]
    # 第二轮：线索台账、NPC 记忆、封路都变了——全是易变档
    ws2 = {
        "clue_ledger": {"cl_letter": {"status": "known"}},
        "npc_memory": {"n_boss": {"attitude": "戒备", "recent": ["你追问了信的下落"]}},
        "blocked_scenes": {"s_shop": "卷帘门拉下来了"},
    }
    return _turn(turn1, None), _turn(turn2, ws2)


def test_连续两轮快照之前逐字一致(tail):
    kw1, kw2 = _two_turns()
    m1 = ctx.build_kp_context(**kw1)
    m2 = ctx.build_kp_context(**kw2)
    i1, i2 = _snapshot_index(m1), _snapshot_index(m2)
    # 易变内容确实变了，否则这条测试证明不了什么
    assert m1[i1]["content"] != m2[i2]["content"]
    # 第一轮快照之前的每条消息，在第二轮里原样出现在同一位置
    assert strip_provider_keys(m1[:i1]) == strip_provider_keys(m2[:i1])
    assert i2 > i1                                  # 第二轮多出的历史接在后面


def test_旧装配下系统提示每轮都变(monkeypatch):
    """对照组：开关关闭时易变档在系统提示里，第一条消息就不同——整段历史缓存失效。"""
    kw1, kw2 = _two_turns()
    m1 = _build(monkeypatch, False, kw1)
    m2 = _build(monkeypatch, False, kw2)
    assert m1[0]["content"] != m2[0]["content"]


def test_快照不改变格式提醒的触发条件(monkeypatch):
    """历史只有一条玩家消息时旧装配不插格式提醒；快照不该被当成历史凑数。"""
    kw = _turn([_player_action(1, "我推开门。")], {"blocked_scenes": {"s_shop": "锁着"}})
    on = _build(monkeypatch, True, kw)
    off = _build(monkeypatch, False, kw)
    has_fmt = [any("[格式提醒]" in (m.get("content") or "") for m in x) for x in (on, off)]
    assert has_fmt[0] == has_fmt[1]


# ── Anthropic：历史末尾的第 3 个断点 ──────────────────────────────────


@pytest.fixture
def provider():
    return AnthropicProvider(model="claude-opus-5", api_key="test")


def test_anthropic_在稳定前缀终点打断点(provider, tail):
    kw1, _ = _two_turns()
    msgs = ctx.build_kp_context(**kw1)
    blocks, rest = provider._split_system(msgs)
    # 系统提示只剩静态+半静态两块，各一个断点
    assert len(blocks) == 2 and all("cache_control" in b for b in blocks)
    marked = [m for m in rest if isinstance(m["content"], list)
              and m["content"][-1].get("cache_control")]
    assert len(marked) == 1
    assert marked[0]["content"][-1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    # 断点之后紧跟的就是快照（已转成 user）
    j = rest.index(marked[0])
    assert rest[j + 1]["content"].startswith(ctx.KP_SNAPSHOT_LABEL)
    # 进程内元数据一律不出请求体；原消息不被就地修改
    assert not any(CACHE_BREAK_KEY in m for m in rest)
    assert any(m.get(CACHE_BREAK_KEY) for m in msgs)


def test_anthropic_断点打在列表内容的最后一块(provider):
    _, rest = provider._split_system([
        {"role": "system", "content": "系统"},
        {"role": "user", "content": [{"type": "text", "text": "甲"}, {"type": "text", "text": "乙"}],
         CACHE_BREAK_KEY: True},
        {"role": "user", "content": "末尾"},
    ])
    assert "cache_control" not in rest[0]["content"][0]
    assert rest[0]["content"][1]["cache_control"]["type"] == "ephemeral"


def test_anthropic_无标记时行为不变(provider):
    _, rest = provider._split_system([
        {"role": "system", "content": "系统"},
        {"role": "user", "content": "玩家"},
        {"role": "system", "content": "[格式提醒] x"},
    ])
    assert rest == [{"role": "user", "content": "玩家"}, {"role": "user", "content": "[格式提醒] x"}]


# ── 缓存读写量的归一（turn_context 落库口径）──────────────────────────


@pytest.mark.parametrize("usage,expect", [
    ({"prompt_tokens": 1000, "cache_read_input_tokens": 900, "cache_creation_input_tokens": 50},
     {"cache_read_tokens": 900, "cache_write_tokens": 50}),                       # Anthropic（已归一）
    ({"prompt_tokens": 1000, "prompt_cache_hit_tokens": 800, "prompt_cache_miss_tokens": 200},
     {"cache_read_tokens": 800, "cache_write_tokens": 200}),                      # DeepSeek
    ({"prompt_tokens": 1000, "prompt_tokens_details": {"cached_tokens": 640}},
     {"cache_read_tokens": 640, "cache_write_tokens": 0}),                        # OpenAI
    ({"prompt_tokens": 1000}, {"cache_read_tokens": 0, "cache_write_tokens": 0}),  # 不报缓存
])
def test_缓存读写量按服务商归一(usage, expect):
    assert _cache_usage(usage) == expect
