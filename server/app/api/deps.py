from __future__ import annotations

from typing import NoReturn

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader
from sqlalchemy.orm import Session

from app.models.character import Character
from app.models.session import GameSession
from app.services import net_access, session_service


_PLAYER_TOKEN_HEADER = APIKeyHeader(name="X-Player-Token", auto_error=False)


def player_token(
    x_player_token: str | None = Security(_PLAYER_TOKEN_HEADER),
) -> str | None:
    """读取玩家身份 token（局域网 MVP：明文 bearer，无鉴权）。

    前端在 localStorage 生成 UUID，并以 ``X-Player-Token`` 头随请求带上。
    """
    return x_player_token


def require_local_client(request: Request) -> None:
    """只允许房主本机调用：管理本机资产的端点用它把关。

    「管理本机资产」指 AI 配置和素材库的增删改——这些是房主在自己机器上的事，
    客人没有任何正当理由去动房主的。此前它们完全没有把关，于是客人模式下：

    - 客人的设置页显示的是**房主的** AI 配置，连「显示密钥」都能点，房主的 API key
      就这么给出去了；
    - 客人能删房主的模组、角色、规则书，能触发生图/解析烧房主的额度。

    这不是「公网才有的问题」——ADR-001 说的可信局域网是「相信朋友不捣乱」，
    不该延伸到「默认把凭据给朋友看」。

    按**来源**而不是按 token 判定：token 是客户端自造的明文串、可以随便伪造，
    而请求来自不来自回环是传输层的事实。

    判定走 ``net_access.peer_kind`` 而不是「源 IP 是回环」：内置直连隧道会把远端
    客人的请求以 ``127.0.0.1`` 反代进来，只看 IP 的话他们会原地变成房主。
    """
    client = request.client.host if request.client else None
    if net_access.peer_kind(client, request.headers) != "local":
        raise HTTPException(403, "此操作只能在房主本机进行")


def require_session_viewer(
    db: Session,
    session_id: str,
    token: str | None,
):
    """统一读取授权：返回可读会话，否则抛出统一的 404/403。"""
    session = session_service.get_session(db, session_id)
    if session is None:
        raise HTTPException(404, "会话不存在")
    if not session_service.can_view_session(db, session_id, token):
        raise HTTPException(403, "无权读取该会话")
    return session


def _raise_actor_error(error: ValueError) -> NoReturn:
    detail = str(error)
    status_code = 404 if detail == "房间不存在" else 403
    raise HTTPException(status_code, detail) from error


def require_session_actor(
    db: Session,
    session_id: str,
    token: str | None,
    acting_character_id: str | None,
) -> Character:
    """统一行动授权：校验显式角色属于当前 token 的真人席位。"""
    try:
        return session_service.resolve_actor(
            db, session_id, token, acting_character_id,
        )
    except ValueError as error:
        _raise_actor_error(error)


def require_session_ooc_actor(
    db: Session,
    session_id: str,
    token: str | None,
    acting_character_id: str | None,
) -> tuple[str | None, str]:
    """大厅 OOC 授权：角色可选，但必须属于当前真人或 KP 席位。"""
    try:
        return session_service.resolve_ooc_actor(
            db, session_id, token, acting_character_id,
        )
    except ValueError as error:
        _raise_actor_error(error)


def require_session_token_actor(
    db: Session,
    session_id: str,
    token: str | None,
) -> Character:
    """统一无角色参数写授权：按 token 解析真人席位，兼容纯本机旧会话。"""
    try:
        return session_service.resolve_token_actor(db, session_id, token)
    except ValueError as error:
        _raise_actor_error(error)


def require_session_manager(
    db: Session,
    session_id: str,
    token: str | None,
    *,
    detail: str = "只有房主可以管理该会话",
) -> GameSession:
    """统一房主管理授权，兼容没有席位归属的纯本机旧会话。"""
    session = session_service.get_session(db, session_id)
    if session is None:
        raise HTTPException(404, "会话不存在")
    if not session_service.can_manage_session(db, session_id, token):
        raise HTTPException(403, detail)
    return session


def require_session_host(
    db: Session,
    session_id: str,
    token: str | None,
    *,
    detail: str = "只有房主可以执行该操作",
) -> GameSession:
    """统一严格房主授权；与 manager 不同，不放行无归属旧会话。"""
    session = session_service.get_session(db, session_id)
    if session is None:
        raise HTTPException(404, "会话不存在")
    if not session_service.is_host(db, session_id, token):
        raise HTTPException(403, detail)
    return session


def require_session_kp(
    db: Session,
    session_id: str,
    token: str | None,
) -> GameSession:
    """真人 KP 专用授权；KP 席与普通玩家席位权限严格分离。"""
    try:
        return session_service.authorize_kp(db, session_id, token)
    except ValueError as error:
        _raise_actor_error(error)
