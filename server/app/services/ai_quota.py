"""房间级 AI 配额：限制**单个房间**在时间窗内能触发多少次生成。

要解决的是速率限制解决不了的那个问题：房内玩家用正常游戏动作（发言、投骰、推进回合）
就会驱动 AI，全部烧房主配置的额度。按来源 IP 限速对此无能为力——那是「防外人敲门」，
这里要防的是「已经进门的人一直点单」。两者不能互相冒充。

**默认关闭。** 单机自己玩不该被限；只有房主邀请了别人、且担心额度时才打开。
打开后配额作用于**整个房间**（不区分是谁触发的），因为在生成入口这一层没有可靠的
行为归属——真人发言、AI 队友回合、战斗续跑都会走到同一处。房主按自己能接受的
额度设上限即可。

计数用 ``limits``（slowapi 的底层库，已在依赖里）的滑动窗口 + 内存存储：
key 是房间 id 而不是请求属性，所以用不上 slowapi 那层 HTTP 封装，直接用底层刚好。
计数在内存里，重启即清零——对「防跑飞」这个目的足够，也不必为它引入持久化。
"""

from __future__ import annotations

import json
import logging

from limits import parse, storage, strategies

from app.config import settings

logger = logging.getLogger(__name__)

SETTINGS_FILE = settings.db_path.parent / "ai_quota.json"

# 默认给得宽松：打开配额是为了拦住跑飞的情况，不是为了卡正常游戏节奏。
DEFAULT_LIMIT = "100/hour"

_storage = storage.MemoryStorage()
_limiter = strategies.MovingWindowRateLimiter(_storage)

_cached: dict | None = None


class QuotaExceeded(Exception):
    """房间在时间窗内触发的生成次数已达上限。由 API 层映射成 429。"""

    def __init__(self, room_id: str, limit_spec: str) -> None:
        super().__init__(f"房间 {room_id} 的 AI 配额已用尽（{limit_spec}）")
        self.room_id = room_id
        self.limit_spec = limit_spec


def _read() -> dict:
    global _cached
    if _cached is not None:
        return _cached
    try:
        data = json.loads(SETTINGS_FILE.read_text("utf-8"))
    except FileNotFoundError:
        data = {}
    except (OSError, json.JSONDecodeError):
        logger.warning("AI 配额设置读取失败，按「未启用」处理：%s", SETTINGS_FILE)
        data = {}
    _cached = {
        "enabled": bool(data.get("enabled")),
        "limit": _valid_limit(data.get("limit")),
    }
    return _cached


def _valid_limit(spec: object) -> str:
    """坏配置回落到默认值，而不是让整个房间卡死或整个功能失效。"""
    if not isinstance(spec, str) or not spec.strip():
        return DEFAULT_LIMIT
    try:
        parse(spec)
    except Exception:
        logger.warning("AI 配额上限格式无效，回落到 %s：%r", DEFAULT_LIMIT, spec)
        return DEFAULT_LIMIT
    return spec


def policy() -> dict:
    return dict(_read())


def set_policy(enabled: bool, limit: str | None = None) -> dict:
    global _cached
    current = _read()
    data = {
        "enabled": bool(enabled),
        "limit": _valid_limit(limit if limit is not None else current["limit"]),
    }
    SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _cached = data
    return dict(data)


def reset_cache() -> None:
    """丢弃设置缓存（测试与外部改文件后手动刷新用）。"""
    global _cached
    _cached = None


def reset_counters() -> None:
    """清空计数（测试用）。"""
    _storage.reset()


def check_and_consume(room_id: str) -> None:
    """记一次生成；超额则抛 ``QuotaExceeded``。未启用时直接放行。"""
    conf = _read()
    if not conf["enabled"]:
        return
    item = parse(conf["limit"])
    if not _limiter.hit(item, room_id):
        raise QuotaExceeded(room_id, conf["limit"])


def remaining(room_id: str) -> int | None:
    """本房间窗口内还剩多少次。未启用时为 None。"""
    conf = _read()
    if not conf["enabled"]:
        return None
    return _limiter.get_window_stats(parse(conf["limit"]), room_id).remaining
