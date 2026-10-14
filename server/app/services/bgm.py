"""背景音乐（BGM）：氛围标签、场景默认氛围、KP 临时覆盖、曲库扫描。

核心取舍：**KP（无论 AI 还是真人）只挑「氛围」，不挑「曲子」**。音频是开局前就备好的资源，
与谁当 KP 无关；跑团时要做的判断只是「此刻该是什么氛围」，这件事用一个封闭枚举表达，
AI 不可能编出不存在的曲子，曲库也可以随时整套替换而不影响任何叙事逻辑。

氛围由三层决定，越往下越依赖临场判断：
1. 战斗 / 追逐：前端按自己已有的战斗、追逐状态直接切，后端不参与（见 useBgm）；
2. 场景默认：模组场景的 ``mood``（导入时由模型标注）；没标就按 ``danger`` 推一个；
   两者都随剧情 flag 的 states 变体覆盖，与配图、危险度同一套解析；
3. KP 临时覆盖：``set_mood`` 落进 ``world_state.bgm_override``，**绑定到当时的场景**——
   换了场景自动失效，不用谁记得去撤。

曲库按目录扫描：``<根>/<氛围>/*.mp3|ogg|m4a|wav``。内置曲库在 ``server/assets/bgm``，
用户曲库在数据目录的 ``bgm/``；某个氛围下用户放了曲子，就整组替换内置的那一组。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from app.config import settings
from app.services.entity_state import active_flags, resolve_state

# 氛围标签 → 中文名。顺序即界面上的展示顺序。
MOODS: dict[str, str] = {
    "calm": "安宁",
    "explore": "调查",
    "tense": "不安",
    "horror": "恐怖",
    "mythos": "神话",
    "sorrow": "悲伤",
    "combat": "战斗",
    "chase": "追逐",
}

# KP 可以手动指定的氛围。战斗 / 追逐由系统按状态自动切，不给 KP 用——
# 否则「叙述里打起来了但没调 start_combat」时音乐先变，反而掩盖了漏调。
KP_MOODS: tuple[str, ...] = ("calm", "explore", "tense", "horror", "mythos", "sorrow")

# 场景没标 mood 时按危险度推。calm 场景多是调查/日常，用 explore 而不是 calm：
# 「安宁」是要被明确标出来的（旅店歇脚、白天的街道），不是默认值。
_DANGER_TO_MOOD = {
    "calm": "explore",
    "uneasy": "tense",
    "dangerous": "horror",
    "deadly": "horror",
}
DEFAULT_MOOD = "explore"

_OVERRIDE_KEY = "bgm_override"


def normalize_mood(raw: object) -> str | None:
    """容错解析一个氛围值：认英文键，也认中文名；认不出返回 None。"""
    s = str(raw or "").strip().lower()
    if not s:
        return None
    if s in MOODS:
        return s
    for key, label in MOODS.items():
        if s == label:
            return key
    return None


def _find_scene(module, scene_id: str | None) -> dict | None:
    if not scene_id:
        return None
    return next(
        (s for s in (getattr(module, "scenes", None) or [])
         if isinstance(s, dict) and s.get("id") == scene_id),
        None,
    )


def base_scene_mood(module, game_session, scene_id: str | None) -> str:
    """场景的默认氛围（不含 KP 覆盖）：按剧情 flag 解析变体后取 mood，缺省按 danger 推。"""
    scene = _find_scene(module, scene_id)
    if scene is None:
        return DEFAULT_MOOD
    resolved = resolve_state(scene, active_flags(game_session)) if game_session is not None else scene
    explicit = normalize_mood(resolved.get("mood"))
    if explicit:
        return explicit
    return _DANGER_TO_MOOD.get(str(resolved.get("danger") or "").strip().lower(), DEFAULT_MOOD)


def scene_mood(module, game_session, scene_id: str | None) -> str:
    """该场景此刻的氛围：KP 对这个场景下过覆盖就用覆盖，否则用场景默认。"""
    override = (getattr(game_session, "world_state", None) or {}).get(_OVERRIDE_KEY) or {}
    if isinstance(override, dict) and scene_id and override.get("scene_id") == scene_id:
        mood = normalize_mood(override.get("mood"))
        if mood:
            return mood
    return base_scene_mood(module, game_session, scene_id)


def apply_override(ws: dict, scene_id: str, mood: str | None) -> dict:
    """world_state 的纯函数更新：给 scene_id 设覆盖；mood 为 None 即撤销覆盖、回到场景默认。"""
    ws = dict(ws or {})
    if mood is None:
        ws.pop(_OVERRIDE_KEY, None)
    else:
        ws[_OVERRIDE_KEY] = {"scene_id": scene_id, "mood": mood}
    return ws


# ---------------------------------------------------------------------------
# 曲库
# ---------------------------------------------------------------------------

_AUDIO_EXTS = (".mp3", ".ogg", ".m4a", ".wav")
# 文件名白名单：拼进 URL 又要回到磁盘上找，宁窄勿宽，杜绝路径穿越。
# 允许中文：用户自己放的曲子多半是中文名。
_NAME_RE = re.compile(r"^[\w一-鿿][\w一-鿿 .()\-]{0,120}$")

SOURCES = ("builtin", "user")


def library_root(source: str) -> Path:
    """内置曲库在 server/assets/bgm（入库素材，desktop.spec 原样打进包）；用户曲库在数据目录 bgm/。"""
    if source == "builtin":
        if getattr(sys, "frozen", False):   # 打包后在 PyInstaller 解包目录（见 desktop.spec）
            return Path(sys._MEIPASS) / "assets" / "bgm"  # type: ignore[attr-defined]
        return Path(__file__).resolve().parents[2] / "assets" / "bgm"
    return settings.db_path.parent / "bgm"


def _valid_name(name: str) -> bool:
    return bool(_NAME_RE.fullmatch(name)) and name.lower().endswith(_AUDIO_EXTS)


def _scan(source: str) -> dict[str, list[str]]:
    root = library_root(source)
    out: dict[str, list[str]] = {}
    for mood in MOODS:
        d = root / mood
        if not d.is_dir():
            continue
        names = sorted(p.name for p in d.iterdir() if p.is_file() and _valid_name(p.name))
        if names:
            out[mood] = names
    return out


def list_tracks() -> dict[str, list[str]]:
    """各氛围可播放的曲目 URL。用户曲库里某氛围有曲子，就整组替换内置的那一组。"""
    from urllib.parse import quote

    builtin = _scan("builtin")
    user = _scan("user")
    out: dict[str, list[str]] = {}
    for mood in MOODS:
        source = "user" if user.get(mood) else "builtin"
        names = (user if source == "user" else builtin).get(mood) or []
        if names:
            out[mood] = [f"/api/bgm/{source}/{mood}/{quote(n)}" for n in names]
    return out


def track_path(source: str, mood: str, name: str) -> Path | None:
    """把一个曲目 URL 的三段还原成磁盘路径；任何一段不合法都返回 None。"""
    if source not in SOURCES or mood not in MOODS or not _valid_name(name):
        return None
    root = library_root(source) / mood
    path = (root / name).resolve()
    if path.parent != root.resolve() or not path.is_file():
        return None
    return path
