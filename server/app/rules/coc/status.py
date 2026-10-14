"""CoC 角色状态定义（键 + 中文标签）。

键为稳定英文标识，落库与代码判断用；标签供前端展示。
重伤/昏迷来自战斗与生命值；临时/不定期/永久疯狂来自理智系统。
"""

CHARACTER_STATUSES: list[dict[str, str]] = [
    {"value": "active", "label": "正常"},
    {"value": "major_wound", "label": "重伤"},
    {"value": "unconscious", "label": "昏迷"},
    {"value": "dead", "label": "死亡"},
    {"value": "temporary_insanity", "label": "临时疯狂"},
    {"value": "indefinite_insanity", "label": "不定期疯狂"},
    {"value": "permanent_insanity", "label": "永久疯狂"},
]

# 历史遗留值 → 新值（incapacitated 旧义≈重伤）
LEGACY_STATUS_ALIAS = {"incapacitated": "major_wound"}

VALID_STATUS_VALUES = {s["value"] for s in CHARACTER_STATUSES}

#: 状态严重度：落疯狂等新状态时只升不降——疯狂不覆盖更严重的既有状态（昏迷/死亡），
#: 永久疯狂也不被临时疯狂降级。
STATUS_SEVERITY: dict[str, int] = {
    "active": 0, "major_wound": 1, "temporary_insanity": 2,
    "indefinite_insanity": 3, "unconscious": 4, "permanent_insanity": 5, "dead": 6,
}
