"""NPC 的统一出口：所有有名 NPC 的开口/行动都从这里走。

此前 NPC 有四条路径：npc_act、KP 的 say()、CombatAgent、BackstageAgent。它们各自
拼上下文、各自更新记忆，导致同一个 NPC 在不同场景下人格与知识边界不一致。本模块先把
**对话/行动出口**收成一个 NpcRuntime：只使用 NPC 自己的人设、私有记忆、可见事件与公开
场景，不接收 KP 的 TurnPlan/truth/线索台账。战斗战术与幕后推演仍是独立模式，但共享同一份
world_memory 中的 NPC 互动史。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.ai.agents.npc_agent import NPCAgent
from app.ai.context import build_npc_context
from app.models.character import Character
from app.models.module import Module
from app.models.session import GameSession
from app.services import illustration_service, npc_identity, session_service, turn_context, world_memory
from app.services.event_protocol import make_chunk as _make_chunk

_attach_npc_portrait = illustration_service._attach_npc_portrait
_apply_world_memory = turn_context._apply_world_memory


class NpcRuntime:
    """一个会话内的 NPC 行动运行时；只暴露「让这个 NPC 按自己的视角回应」这一件事。"""

    def __init__(
        self, db: Session, session_id: str, game_session: GameSession,
        module: Module, llm, player_char: Character,
    ) -> None:
        self.db = db
        self.session_id = session_id
        self.game_session = game_session
        self.module = module
        self.llm = llm
        self.player_char = player_char

    async def reply(self, npc_id: str, trigger: str) -> tuple[list[str], str]:
        """让 NPC 基于自己的可见上下文回应；台词落库、广播、写互动史。"""
        events = session_service.get_session_events(self.db, self.session_id)
        messages = build_npc_context(
            npc_id, self.game_session, self.module, events, trigger_context=trigger,
        )
        npc_def = next(
            (n for n in (self.module.npcs or []) if n.get("id") == npc_id),
            None,
        )
        true_name = npc_def["name"] if npc_def else npc_id
        line = await NPCAgent(self.llm, npc_id).respond(messages)
        # 玩家还没认出这东西时，气泡不能替他报出真名；这句台词本身也要参与判据，
        # 因为他可能在这一句里自报家门。
        shown_name = npc_identity.build_masker(
            self.db, self.session_id, self.module,
        )(true_name, extra_prose=line)
        event = session_service.add_event(
            self.db, self.session_id, "dialogue", line,
            actor_id=npc_id, actor_name=shown_name,
            visibility=[npc_id, self.player_char.id],
        )
        _attach_npc_portrait(self.db, self.session_id, self.module, event)
        portrait = (event.metadata_ or {}).get("portrait")
        chunks = [_make_chunk(
            "dialogue", line, actor_name=shown_name,
            event_id=event.id, actor_id=npc_id,
            metadata={"portrait": portrait} if portrait else None,
        )]
        _apply_world_memory(
            self.db, self.game_session,
            lambda ws: world_memory.record_npc_interaction(
                ws, npc_id, event.sequence_num, f"受场景触发而行动/开口：{trigger}",
            ),
        )
        return chunks, line
