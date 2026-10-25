"""回合推进后执行已确认的前往动作；在上下文构建前完成位置写入。"""
from sqlalchemy.orm import Session
from app.models.character import Character
from app.models.module import Module
from app.models.session import GameSession
from app.services import movement, session_service
from app.services.turn_context import _current_turn_events


def commit_pending_travel(db: Session, session_id: str, turn: list | None = None) -> None:
    """把本回合已转正的『前往』动作落成确定性位置同步。

    大地图暂存式前往（stash=True）只记一条带 ``travel``/``scene_id`` 元数据的 pending_turn 动作；
    推进本回合后本函数在建 KP 上下文前把对应角色搬到目标场景，KP 随即以正确位置叙述抵达见闻。
    """
    if turn is None:
        turn = _current_turn_events(session_service.get_session_events(db, session_id))
    trips = [
        (ev.actor_id, str((ev.metadata_ or {})["scene_id"])) for ev in turn
        if (ev.metadata_ or {}).get("travel") and (ev.metadata_ or {}).get("scene_id") and ev.actor_id
    ]
    if not trips:
        return
    game_session = db.get(GameSession, session_id)
    if game_session is None:
        return
    module = db.get(Module, game_session.module_id)
    party = session_service.get_party_members(db, session_id)
    host = db.get(Character, game_session.player_character_id)
    if host is not None and host not in party:
        party.insert(0, host)
    for actor_id, scene_id in trips:
        actor = next((c for c in party if c.id == actor_id), None) or db.get(Character, actor_id)
        if actor is None:
            continue
        # 与自然语言移动同一套执行语义：同处的人要么按默认跟随一起走，要么被写实在原处。
        movement.travel(db, session_id, game_session, module, actor, scene_id, party)
