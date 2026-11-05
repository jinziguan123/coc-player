"""KP 裁定手册的轻量守卫测试。

不验证 LLM 行为，只守住两条底线：
1. 系统提示词里包含核心裁定手册各分区（信息分层 + 检定先行纪律必须在场）。
2. 静态裁定手册不无节制膨胀：系统提示不设上限、不丢段，但它越长，事件区（剧情历史）
   能分到的就越少，且每轮都要付这份输入。
"""

import re

from app.ai.context import _estimate_tokens, SYSTEM_TOKENS_ESTIMATE
from app.ai.prompts.kp_system import KP_SYSTEM_PROMPT


def test_rulebook_sections_present():
    s = KP_SYSTEM_PROMPT
    for marker in (
        "技能检定流程",
        "属性（特征）检定",
        "调查与信息揭示纪律",
        "检定先行",
        "信息分层",
        "理智检定",
        "战斗",
        "伤害、濒死与急救",
        "常见情境裁定",
        "孤注一掷",
    ):
        assert marker in s, f"裁定手册缺少分区：{marker}"


def test_say_marker_is_mandatory_for_all_dialogue():
    """台词归属治本（P2-1）：契约要求一切 NPC 台词都用 [SAY] 标记，把归属从『猜』变成
    『读标记』，启发式降级为兜底。守住这条，防契约被改回「仅多说话人才标」。"""
    s = KP_SYSTEM_PROMPT
    assert "一律" in s and "[SAY: who=完整姓名]" in s
    assert "不是可选项" in s
    # 明确点出裸引号会被启发式误判（激励全量标记）
    assert "启发式" in s and "猜" in s


def test_characteristic_checks_and_proactive_guidance():
    """属性检定入册 + KP 主动推进指引：九维属性可作 [DICE_CHECK] 的 skill、
    典型用途映射在册；卡关/需专业知识时 KP 应主动发起灵感/教育检定，不干等玩家申请。"""
    s = KP_SYSTEM_PROMPT
    # 九维属性典型用途映射（中文名直接作 skill）
    for kw in ("力量", "体质", "敏捷", "外貌", "意志", "灵感", "知识", "幸运"):
        assert kw in s, f"属性检定小节缺少属性：{kw}"
    assert "系统自动按属性值判定" in s
    # 主动推进：困在原地/多轮无进展/需专业知识 → KP 主动发起检定
    assert "应当主动" in s
    assert "不要干等玩家想起来申请" in s


def test_rulebook_static_leaves_budget_for_module_data():
    static = re.sub(r"\{[a-z_]+\}", "", KP_SYSTEM_PROMPT)
    static_tokens = _estimate_tokens(static)
    # 系统提示整体按 SYSTEM_TOKENS_ESTIMATE 估算来给事件区留预算；静态手册之外还要接模组数据 +
    # 模组/规则原文摘录 + 线索台账 + NPC 记忆 + 幕后动态 + handout 清单（合计可达数千 token）。
    # 减数 6000 给这些下游内容留出估算余量；手册确实需要长时，同步抬 SYSTEM_TOKENS_ESTIMATE。
    assert static_tokens < SYSTEM_TOKENS_ESTIMATE - 6000, (
        f"KP 系统提示词过长（{static_tokens} token），下游注入内容预算不足"
    )
