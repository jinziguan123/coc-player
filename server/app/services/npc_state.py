"""NPC 的单一状态视图：对话、战斗、幕后共享同一份身份/记忆/知识边界。

之前 `build_npc_context` 直接拼人设与记忆，CombatAgent 又单独读记忆，BackstageAgent
自己拿模块 NPC 卡。这个模块把「一个 NPC 此刻是谁、记得什么、在哪」收成一个只读快照，
让各模式至少从同一份状态出发，而不是三套影子数据。
"""
from __future__ import annotations

from dataclasses import dataclass

from app.models.module import Module
from app.models.session import GameSession
from app.services import world_memory


def _as_text(value) -> str:
    return "\n".join(value) if isinstance(value, list) else (value or "")


@dataclass(frozen=True)
class NpcState:
    npc_id: str
    name: str
    description: str
    personality: str
    background: str
    secrets: str
    location: str
    memory: str

    @classmethod
    def load(cls, module: Module, session: GameSession, npc_id: str) -> "NpcState":
        npc_def = next(
            (n for n in (module.npcs or []) if str(n.get("id") or "") == npc_id),
            None,
        )
        if npc_def is not None:
            # 与 build_npc_context 同口径解析当前 flags（已暴露/敌对/死亡等），否则
            # 对话看到的是「当前形态」，战斗状态看到的还是原始卡。
            try:
                from app.ai.context import _active_flags, _resolve_state
                npc_def = _resolve_state(npc_def, _active_flags(session))
            except Exception:
                pass
        if npc_def is None:
            npc_def = next(
                (c for c in world_memory.promoted_npc_cards(session.world_state or {})
                 if str(c.get("id") or "") == npc_id),
                None,
            )
        npc_def = npc_def or {}
        try:
            memory = world_memory.format_npc_self_memory(
                session.world_state or {}, npc_id,
            )
        except Exception:
            memory = ""
        return cls(
            npc_id=npc_id,
            name=str(npc_def.get("name") or npc_id),
            description=_as_text(npc_def.get("description")),
            personality=_as_text(npc_def.get("personality")),
            background=_as_text(npc_def.get("background")),
            secrets=_as_text(npc_def.get("secrets")),
            location=str(
                npc_def.get("initial_location")
                or session.current_scene_id
                or ""
            ),
            memory=memory,
        )

    def persona_text(self) -> str:
        """给任何 NPC 模式共用的人设段；空字段不占 token。"""
        parts = [f"姓名：{self.name}"]
        for label, value in (
            ("描述", self.description),
            ("性格", self.personality),
            ("背景", self.background),
            ("秘密", self.secrets),
        ):
            if value:
                parts.append(f"{label}：{value}")
        return "\n".join(parts)
