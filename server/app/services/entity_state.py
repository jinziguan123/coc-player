"""模组实体（场景 / NPC）的剧情状态解析：纯函数，不碰数据库、不碰 ai/。

从 ai/context 下沉到这里，是因为领域服务（如 bgm）也要按剧情 flag 解析场景变体，
而领域层不得反向依赖 ai/（见 tests/test_layering.py）。
"""

from __future__ import annotations


def active_flags(session) -> set[str]:
    """当前已激活的剧情标志集合（world_state.flags 容忍 dict / list 两种形态）。"""
    raw = (getattr(session, "world_state", None) or {}).get("flags")
    if isinstance(raw, dict):
        return {k for k, v in raw.items() if v}
    if isinstance(raw, (list, set, tuple)):
        return set(raw)
    return set()


def resolve_state(entity: dict, flags: set[str]) -> dict:
    """按已激活 flags 把 entity.states 里命中的变体依次覆盖到基础字段上（后命中者优先）。

    变体形如 ``{"when": ["flag_a", ...], <要覆盖的字段>}``：when 内的 flag 全部已激活才生效，
    缺省/空 when 视为恒命中。无 states 或无任何命中时返回基础字段本身（向后兼容）。
    """
    states = entity.get("states")
    if not states:
        return entity
    base = {k: v for k, v in entity.items() if k != "states"}
    for st in states:
        when = st.get("when") or []
        if isinstance(when, str):
            when = [when]
        if all(w in flags for w in when):
            base.update({k: v for k, v in st.items() if k != "when"})
    return base
