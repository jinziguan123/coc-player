"""LLM Provider 工厂：按激活配置创建对应厂商的 Provider。

与具体厂商解耦——各 Provider 实现在 app.ai.providers 下，本文件只负责「按配置选谁」。
为兼容既有导入，仍从此处再导出 OpenAICompatProvider。
"""

import logging

from app.ai import profile_store
from app.ai.provider import LLMProvider
from app.ai.providers.openai_compat import OpenAICompatProvider
from app.config import settings

logger = logging.getLogger(__name__)

__all__ = ["get_llm", "get_fast_llm", "check_llm_pair", "OpenAICompatProvider"]


def _provider_from_profile(profile) -> LLMProvider:
    if profile.protocol == "anthropic":
        from app.ai.providers.anthropic import AnthropicProvider
        provider: LLMProvider = AnthropicProvider(
            model=profile.model_name,
            base_url=profile.base_url,
            api_key=profile.api_key,
            extra_headers=getattr(profile, "extra_headers", None),
        )
    else:
        provider = OpenAICompatProvider(
            model=profile.model_name,
            base_url=profile.base_url,
            api_key=profile.api_key,
            vision=getattr(profile, "vision", False),
            reasoning_effort=getattr(profile, "reasoning_effort", ""),
            thinking_disabled=getattr(profile, "thinking_disabled", False),
            extra_headers=getattr(profile, "extra_headers", None),
        )
    # 生图不在这里装配——它有独立的配置与后端，见 app.ai.image_gen.get_image_llm()。
    return provider


def provider_from_profile(profile) -> LLMProvider:
    """公开入口：按任意 profile 建 Provider（设置页测试连接/测试生图用，保证与运行时同一装配）。"""
    return _provider_from_profile(profile)


def get_llm() -> LLMProvider:
    """根据激活的配置创建对应的 LLM Provider。

    - protocol="anthropic" -> AnthropicProvider
    - protocol="openai"（默认）-> OpenAICompatProvider（兼容 OpenAI API）

    AI 配置的唯一真源是设置页（ai_settings.json 的激活 profile）；不再有 .env 回退。
    没有激活配置时抛出可读错误，由生成路径兜成「请到设置页配置 AI」的提示。
    """
    profile = profile_store.load_active_profile()
    if not profile:
        raise ValueError("未配置可用的 AI 模型：请到设置页添加并激活一个 AI 配置。")
    return _provider_from_profile(profile)


def get_fast_llm() -> LLMProvider:
    """结构化副任务（planner / 滚动摘要 / 幕后推演 / 生图与地图提示词）用的「快模型」。

    设置页可把某个配置标记为快模型（is_fast）；未标记时回落到主模型（行为与从前一致）。
    终检与去口癖也会先走它，改写不达标再回退主模型（见 ``check_llm_pair``）。

    判据是**产出会不会原样摆在玩家面前**：会的一律走主模型——KP 叙事、NPC 台词，
    以及 **AI 队友的言行**（他们和真人玩家发一样的气泡，吃同样的文笔与分寸感；
    早先图快让队友走快模型，结果演出来发木、同桌感塌掉）。快模型只接产结构、
    玩家看不到的那些活。
    """
    profile = profile_store.load_fast_profile()
    if not profile:
        return get_llm()
    return _provider_from_profile(profile)


def check_llm_pair(main_llm: LLMProvider) -> tuple[LLMProvider, LLMProvider | None]:
    """终检/去口癖这类「先快后主」任务的两个模型：返回 ``(首选, 回退)``。

    回退为 ``None`` 表示「只跑一次主模型」——没单独配快模型、开关关掉、或读取配置失败时
    都是这个口径。**绝不能把同一份请求对同一个模型发两遍**：那既不会更快，还会让一次检查
    变成两次调用。调用方拿到 ``None`` 就当普通单模型路径走。

    为什么这两步可以先用快模型：它们都是「判断题 + 定点改写」，不是 KP 那种要文笔的
    创作；快模型槽位通常关着思考，而这两步实测各要 20~40s。回退条件由调用方定，
    但都必须是确定性的（解析失败、改写稿为空/长度失真/口癖没减少），不能靠感觉。
    """
    if not settings.checks_use_fast_model:
        return main_llm, None
    try:
        if profile_store.load_fast_profile() is None:
            return main_llm, None
        return get_fast_llm(), main_llm
    except Exception:  # noqa: BLE001 — 取快模型失败不该让检查本身失败
        logger.exception("解析快模型失败，终检/去口癖回落叙事模型")
        return main_llm, None


def get_vision_llm() -> LLMProvider:
    """看图任务（扫描件/图文模组的视觉解析）用的「视觉模型」。

    设置页可把某个配置标记为视觉模型（is_vision）；未标记时回落主模型（行为与从前一致）。

    单开一个槽位是因为这两件事对模型的要求正相反：带团要文笔与工具调用，主模型多是纯文本；
    而解析图文模组只需要一双眼睛，跟文笔无关。绑在一起的后果是「想解析图文模组就得把带团
    模型也换成多模态」——多数人不会为了导一次模组这么折腾，于是图文模组直接解析不了。
    拆开之后，视觉模型可以是云端的（Qwen-VL/GPT-4o/Gemini），也可以是本机跑的。
    """
    profile = profile_store.load_vision_profile()
    if not profile:
        return get_llm()
    # 把某个配置**放进视觉槽位**这个动作，本身就是用户在断言「这个模型会看图」。
    # 还要求他再去编辑表单里勾一次 vision 复选框，是个纯粹的重复劳动，而且失败得很难懂：
    # 名字里没有 -vl 的多模态模型（qwen3.7-plus、gpt-5 这类）会被名字启发式判成纯文本，
    # 于是「我明明设了视觉模型」却报「没有可用于看图的模型」。槽位即能力。
    profile = profile.model_copy(update={"vision": True})
    return _provider_from_profile(profile)
