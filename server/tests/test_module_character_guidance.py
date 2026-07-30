"""模组的车卡建议：归一、落库、可改写。

玩家针对某个模组建角色时需要知道「这个本子想要什么样的调查员」。内容由模组设定
派生、一次生成即长期可用，所以存在模组上而不是每次建卡现算。

**刻意不塞进 parse_module_text**：那次调用的输出已长到需要断点续写，再加字段只会
加剧截断——所以是独立一次小调用，只喂设定摘要、不喂全文。
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (  # noqa: F401 — 注册全部表
    Base,
    Character,
    EventLog,
    GameSession,
    Module,
    SessionParticipant,
)
from app.services import module_service
from app.services.module_service import normalize_character_guidance


@pytest.fixture
def db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'guidance.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


class TestNormalize:
    """界面直接渲染这四个字段，所以在入口处就收敛干净。"""

    def test_keeps_well_formed_payload(self):
        out = normalize_character_guidance({
            "summary": "1920 年代埃及考古队成员",
            "recommended": ["考古学者", "医生"],
            "avoid": ["现代黑客——时代不符"],
            "notes": ["需要有前往埃及的正当理由"],
        })
        assert out["summary"] == "1920 年代埃及考古队成员"
        assert out["recommended"] == ["考古学者", "医生"]
        assert out["avoid"] == ["现代黑客——时代不符"]
        assert out["notes"] == ["需要有前往埃及的正当理由"]

    def test_fills_missing_keys(self):
        # 局部生成（或房主只填了一项）时，缺的字段要有稳定默认值供界面渲染
        assert normalize_character_guidance({"summary": "只有一句"}) == {
            "summary": "只有一句", "recommended": [], "avoid": [], "notes": [],
        }

    def test_drops_non_string_items(self):
        out = normalize_character_guidance({
            "recommended": ["医生", 42, None, {"x": 1}, "  记者  "],
        })
        assert out["recommended"] == ["医生", "记者"]

    def test_drops_blank_items(self):
        out = normalize_character_guidance({"notes": ["", "   ", "有效一条"]})
        assert out["notes"] == ["有效一条"]

    def test_caps_runaway_output(self):
        # 一次跑偏的生成不该把角色创建页撑破
        out = normalize_character_guidance({
            "summary": "长" * 500,
            "recommended": [f"职业{i}" for i in range(50)],
            "notes": ["条" * 500],
        })
        assert len(out["summary"]) == 200
        assert len(out["recommended"]) == 8
        assert len(out["notes"][0]) == 200

    def test_garbage_becomes_empty(self):
        for bad in (None, [], "一段文本", 42):
            assert normalize_character_guidance(bad) == {}


def test_created_module_carries_guidance(db):
    module = module_service.create_module(db, {
        "title": "陵墓",
        "rule_system": "coc",
        "character_guidance": {"summary": "考古队成员", "recommended": ["考古学者", 7]},
    })
    assert module.character_guidance["summary"] == "考古队成员"
    # 落库时就已归一，脏项不会进 DB
    assert module.character_guidance["recommended"] == ["考古学者"]


def test_module_without_guidance_defaults_to_empty(db):
    """历史模组没有这段，读取时要是空 dict 而不是 None——前端直接取 .summary。"""
    module = module_service.create_module(db, {"title": "旧模组", "rule_system": "coc"})
    assert module.character_guidance == {}


def test_host_can_rewrite_guidance(db):
    """AI 出初稿，KP 才是最终裁量。"""
    module = module_service.create_module(db, {
        "title": "陵墓", "rule_system": "coc",
        "character_guidance": {"summary": "AI 写的"},
    })
    updated = module_service.update_module(db, module.id, {
        "title": "陵墓", "character_guidance": {"summary": "房主改的", "notes": ["自定一条"]},
    })
    assert updated.character_guidance["summary"] == "房主改的"
    assert updated.character_guidance["notes"] == ["自定一条"]


def test_omitting_guidance_keeps_existing(db):
    """旧前端不提交这个字段时不能把已有内容清空（与 truth 同一处理）。"""
    module = module_service.create_module(db, {
        "title": "陵墓", "rule_system": "coc",
        "character_guidance": {"summary": "原有的"},
    })
    updated = module_service.update_module(db, module.id, {"title": "陵墓"})
    assert updated.character_guidance["summary"] == "原有的"


@pytest.mark.asyncio
async def test_generation_prompt_carries_setting_not_raw_text(monkeypatch, db):
    """只喂设定摘要，不喂全文——这正是它不加剧主解析截断的原因。"""
    module = module_service.create_module(db, {
        "title": "陵墓",
        "rule_system": "coc",
        "description": "埃及古墓探险",
        "world_setting": {"era": "1924 年", "location": "帝王谷", "tone": "恐怖探险"},
    }, raw_content="这是一大段模组原文" * 500)

    seen = {}

    class FakeLLM:
        async def complete(self, messages, **kwargs):
            seen["prompt"] = messages[0]["content"]
            return '{"summary": "考古队成员", "recommended": ["考古学者"]}'

    monkeypatch.setattr(module_service, "get_llm", lambda: FakeLLM())
    out = await module_service.generate_character_guidance(module)

    assert out["summary"] == "考古队成员"
    prompt = seen["prompt"]
    assert "1924 年" in prompt and "帝王谷" in prompt and "恐怖探险" in prompt
    assert "这是一大段模组原文" not in prompt


@pytest.mark.asyncio
async def test_generation_normalizes_bad_output(monkeypatch, db):
    module = module_service.create_module(db, {"title": "陵墓", "rule_system": "coc"})

    class FakeLLM:
        async def complete(self, messages, **kwargs):
            return '{"summary": 123, "recommended": "不是数组", "notes": ["好的一条", 5]}'

    monkeypatch.setattr(module_service, "get_llm", lambda: FakeLLM())
    out = await module_service.generate_character_guidance(module)

    assert out["summary"] == ""
    assert out["recommended"] == []
    assert out["notes"] == ["好的一条"]
