"""文风 / 画风：解析约定、生效层级与内容红线。"""

import pytest

from app.services import style_presets


class _Obj:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def test_empty_means_no_narrative_injection():
    """没选文风 → 空串 → 调用方不注入，行为与本特性上线前逐字相同（存量存档天然落这一档）。"""
    assert style_presets.narrative_style_prompt("", "") == ""
    assert style_presets.narrative_style_prompt(None, None) == ""


def test_preset_id_resolves_to_its_prompt():
    out = style_presets.narrative_style_prompt("terse", "")
    assert out == style_presets.NARRATIVE_STYLES["terse"]["prompt"]
    assert "短句" in out


def test_non_preset_value_is_used_verbatim_as_custom():
    """预设 id 全是 ASCII slug，自定义是中文/英文原文——不命中预设就当自定义原样用。"""
    custom = "像武侠小说那样写，多用四字短语"
    assert style_presets.narrative_style_prompt(custom, "") == custom


def test_session_overrides_module_default():
    assert style_presets.narrative_style_prompt("plain", "dense") == (
        style_presets.NARRATIVE_STYLES["plain"]["prompt"]
    )
    # 本局留空 → 继承模组默认
    assert style_presets.narrative_style_prompt("", "dense") == (
        style_presets.NARRATIVE_STYLES["dense"]["prompt"]
    )


def test_image_style_always_carries_safety_tail():
    """内容红线追加在用户可编辑部分**之后**，任何预设与自定义都盖不掉。

    OpenAI 兼容的生图接口没有负面提示词参数，那条路径只能靠这句正向兜底。
    """
    for value in ("", "woodcut", "cinematic neon cyberpunk", "nude, explicit"):
        assert style_presets.image_style_suffix(value).endswith(
            style_presets.IMAGE_SAFETY_TAIL,
        )


def test_image_style_falls_back_to_default_preset():
    """生图没有「不加画风」这一档：不给画风词，模型会滚出风格随机的图，场景之间失去一致性。"""
    assert style_presets.image_style_suffix("", "") == style_presets.image_style_suffix()
    assert "ink lineart" in style_presets.image_style_suffix()


@pytest.mark.parametrize("table", [style_presets.NARRATIVE_STYLES, style_presets.IMAGE_STYLES])
def test_preset_ids_are_ascii(table):
    """id 必须是 ASCII slug——「不命中预设即自定义」这条约定靠它和中文自定义文案不互撞。"""
    for key in table:
        assert key.isascii() and key.replace("_", "").isalnum(), key


def test_style_options_shape():
    opts = style_presets.style_options()
    assert {"narrative", "image"} == set(opts)
    for group in opts.values():
        assert group and all({"id", "label", "hint"} <= set(x) for x in group)


def test_module_image_suffix_reads_module_and_session():
    from app.services import module_image_service

    module = _Obj(default_image_style="oil_classic")
    session = _Obj(image_style="woodcut")
    assert "woodcut" in module_image_service.style_suffix_for(module, session)
    assert "oil painting" in module_image_service.style_suffix_for(module)
    # 都没设 → 默认档
    assert module_image_service.style_suffix_for() == style_presets.image_style_suffix()


def test_narrative_style_reaches_kp_prompt(tmp_path):
    """选了文风就要真的进 KP 系统提示词，且带上「叙事纪律优先级更高」的申明。

    少了那句申明，「影视化」「通俗冒险」这类偏戏剧化的档很容易被理解成「可以替玩家演一下」。
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.ai import context as ctx
    from app.models import (  # noqa: F401 — 注册全部表
        Base,
        Character,
        GameSession,
        Module,
    )
    from app.services import session_service

    engine = create_engine(
        f"sqlite:///{tmp_path / 'style.db'}", connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    module = Module(title="M", rule_system="coc", npcs=[], scenes=[])
    hero = Character(name="主角", rule_system="coc", is_player=True)
    db.add_all([module, hero]); db.commit()
    session = session_service.create_session(
        db, module.id, [{"character_id": hero.id, "is_primary": True}],
    )
    session_service.add_event(
        db, session.id, "action", "我推开门", actor_id=hero.id, actor_name=hero.name,
    )
    events = session_service.get_session_events(db, session.id)

    # 没设 → 完全不出现该小节（存量存档行为不变）
    assert "本场文风" not in ctx.build_kp_context(session, module, hero, events)[0]["content"]

    # 模组默认生效
    module.default_narrative_style = "terse"
    sys_module = ctx.build_kp_context(session, module, hero, events)[0]["content"]
    assert "本场文风" in sys_module
    assert style_presets.NARRATIVE_STYLES["terse"]["prompt"] in sys_module
    assert "优先级更高" in sys_module

    # 本局覆盖模组
    session.narrative_style = "像武侠小说那样写"
    sys_session = ctx.build_kp_context(session, module, hero, events)[0]["content"]
    assert "像武侠小说那样写" in sys_session
    assert style_presets.NARRATIVE_STYLES["terse"]["prompt"] not in sys_session
    db.close()
