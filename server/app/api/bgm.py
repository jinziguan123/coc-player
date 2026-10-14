"""背景音乐曲库：列出各氛围的曲目、只读地提供音频文件。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.services import bgm

router = APIRouter(prefix="/api/bgm", tags=["bgm"])


@router.get("/tracks")
def list_tracks():
    """各氛围可播放的曲目 URL，及氛围标签的中文名（前端设置面板用）。"""
    return {"moods": bgm.MOODS, "tracks": bgm.list_tracks()}


@router.get("/credits")
def credits():
    """内置曲目的来源与署名（设置页「音乐致谢」）。"""
    return {"tracks": bgm.builtin_credits()}


@router.get("/{source}/{mood}/{name}")
def get_track(source: str, mood: str, name: str):
    path = bgm.track_path(source, mood, name)
    if path is None:
        raise HTTPException(404, "曲目不存在")
    # 用户曲库里的文件可能被替换成同名新曲，不能像配图那样标 immutable。
    return FileResponse(path, headers={"Cache-Control": "public, max-age=3600"})
