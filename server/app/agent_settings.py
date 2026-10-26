"""本机 agent 高级设置：运行预算与上下文档位。

与 ``ai_settings.json`` 同目录存一份 ``agent_settings.json``，只记用户改过的项。
优先级：配置文件覆盖 > 环境变量 / ``.env``（``config.settings``）> 代码默认。
每次生成开始时经 ``current()`` 读取（按文件修改时间缓存），下一次生成即生效、无需重启。

只依赖标准库、pydantic 与 ``config``；上下文档位各档的具体数值由上下文层（``app.ai.context``）定义，
这里只存档位名。文件损坏或某项越界时该项回落默认并记日志，绝不影响启动。
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from app.config import settings

logger = logging.getLogger(__name__)

SETTINGS_FILE = settings.db_path.parent / "agent_settings.json"
FILE_VERSION = 1

ContextPreset = Literal["economy", "balanced", "full"]
CONTEXT_PRESETS: tuple[str, ...] = ("economy", "balanced", "full")
DEFAULT_CONTEXT_PRESET = "balanced"


@dataclass(frozen=True)
class ParamSpec:
    key: str
    label: str
    description: str
    kind: Literal["int", "float", "bool", "choice"]
    default: Any
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = ()
    unit: str = ""


def _env_default(key: str) -> Any:
    return getattr(settings, key)


#: 规划器墙钟超时与 KP 工具循环步数的代码默认（原为各自模块常量，现以此为准）。
PLANNER_TIMEOUT_DEFAULT = 120.0
KP_TOOL_LOOP_STEPS_DEFAULT = 20

PARAMS: tuple[ParamSpec, ...] = (
    ParamSpec("generation_max_calls", "每次生成最多模型调用", "一次生成（含队友、规划器、KP、终检）累计的模型调用次数上限，到了就收束本轮。",
              "int", None, 8, 200, unit="次"),
    ParamSpec("generation_max_tokens", "每次生成最多 token", "一次生成累计消耗的 token 上限，防止失控的循环烧光额度。",
              "int", None, 50_000, 3_000_000, unit="token"),
    ParamSpec("generation_max_seconds", "每次生成最长时长", "一次生成的墙钟时长上限，超时后收束本轮。",
              "float", None, 60, 1800, unit="秒"),
    ParamSpec("housekeeping_max_seconds", "后台收尾最长时长", "滚动摘要与幕后推演的时长上限；下一回合开头会等它完成。",
              "float", None, 30, 600, unit="秒"),
    ParamSpec("narration_buffer_high_risk", "高风险回合先终检再放出", "本轮有不可泄露的内容时，叙事整段缓冲到终检完成才展示，首字更慢但可拦截泄密。",
              "bool", None),
    ParamSpec("narration_buffer_timeout_seconds", "缓冲等待终检的超时", "高风险缓冲等待终检的最长时间。",
              "float", None, 10, 300, unit="秒"),
    ParamSpec("narration_buffer_on_failure", "终检超时或失败时", "release＝照常放出叙事；withhold＝扣下不展示。",
              "choice", None, choices=("release", "withhold")),
    ParamSpec("checks_use_fast_model", "终检与去口癖先用快模型", "开启时先用快模型做终检与去口癖，不达标再回退主模型。",
              "bool", None),
    ParamSpec("planner_timeout_seconds", "规划器超时", "回合规划器的墙钟上限；慢模型或推理模型经常超时可调大，超时后本轮按旧流程继续。",
              "float", PLANNER_TIMEOUT_DEFAULT, 30, 600, unit="秒"),
    ParamSpec("kp_tool_loop_max_steps", "KP 工具循环最多轮数", "KP 一段叙事里调用工具的最多轮数，超过后要求直接收束本轮。",
              "int", KP_TOOL_LOOP_STEPS_DEFAULT, 4, 40, unit="轮"),
)
PARAMS_BY_KEY = {p.key: p for p in PARAMS}


def default_of(spec: ParamSpec) -> Any:
    """该项的默认值：环境变量 / .env 已设的值优先，否则代码默认。"""
    return spec.default if spec.default is not None else _env_default(spec.key)


def validate_value(spec: ParamSpec, value: Any) -> Any:
    """把一个覆盖值校验并归一成该项的类型；不合法抛 ValueError（附中文原因）。"""
    if spec.kind == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{spec.label}只能是开或关")
        return value
    if spec.kind == "choice":
        if value not in spec.choices:
            raise ValueError(f"{spec.label}只能是 {' / '.join(spec.choices)}")
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{spec.label}必须是数字")
    if spec.kind == "int":
        if int(value) != value:
            raise ValueError(f"{spec.label}必须是整数")
        value = int(value)
    else:
        value = float(value)
    if spec.minimum is not None and value < spec.minimum or spec.maximum is not None and value > spec.maximum:
        # 中西文之间留空格：「8–200 次之间」「50,000–3,000,000 token 之间」「最多 token 须在」
        unit = f" {spec.unit}{' ' if spec.unit.isascii() else ''}" if spec.unit else " "
        sep = " " if spec.label[-1:].isascii() else ""
        raise ValueError(f"{spec.label}{sep}须在 {_fmt(spec.minimum)}–{_fmt(spec.maximum)}{unit}之间")
    return value


def _fmt(number: float | None) -> str:
    """千分位、整数不带小数点（3000000 → 3,000,000，而不是 3e+06）。"""
    if number is None:
        return ""
    return f"{int(number):,}" if float(number).is_integer() else f"{number:,}"


@dataclass(frozen=True)
class AgentSettings:
    """合成后的只读快照：每项已按「文件覆盖 > 环境 > 默认」取值。"""
    generation_max_calls: int
    generation_max_tokens: int
    generation_max_seconds: float
    housekeeping_max_seconds: float
    narration_buffer_high_risk: bool
    narration_buffer_timeout_seconds: float
    narration_buffer_on_failure: str
    checks_use_fast_model: bool
    planner_timeout_seconds: float
    kp_tool_loop_max_steps: int
    context_preset: str
    overrides: dict[str, Any]


# ── 读写 ─────────────────────────────────────────────────────────────

_lock = threading.Lock()
_cache: tuple[float | None, dict] | None = None


def _read_raw() -> dict:
    """读文件；不存在返回空结构，损坏记日志后按空处理（不覆盖原文件）。"""
    global _cache
    try:
        mtime = SETTINGS_FILE.stat().st_mtime
    except FileNotFoundError:
        return {}
    if _cache is not None and _cache[0] == mtime:
        return _cache[1]
    try:
        data = json.loads(SETTINGS_FILE.read_text("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("顶层不是对象")
    except Exception:
        logger.exception("agent 高级设置文件无法解析，按默认运行（文件未改动）：%s", SETTINGS_FILE)
        data = {}
    _cache = (mtime, data)
    return data


def _write_raw(data: dict) -> None:
    """原子写入：临时文件写完再替换，写一半崩溃也不会留下半截 JSON。"""
    global _cache
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".agent_settings.", suffix=".json", dir=str(SETTINGS_FILE.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, SETTINGS_FILE)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    _cache = None


def _valid_overrides(raw: dict) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in (raw.get("overrides") or {}).items() if isinstance(raw.get("overrides"), dict) else ():
        spec = PARAMS_BY_KEY.get(key)
        if spec is None:
            continue   # 未知键：读取时忽略，写回时原样保留（向前兼容）
        try:
            out[key] = validate_value(spec, value)
        except ValueError as error:
            logger.warning("agent 高级设置 %s 的值 %r 无效（%s），按默认运行", key, value, error)
    return out


def _preset_of(raw: dict) -> str:
    preset = raw.get("context_preset", DEFAULT_CONTEXT_PRESET)
    if preset not in CONTEXT_PRESETS:
        logger.warning("agent 高级设置上下文档位 %r 无效，按均衡运行", preset)
        return DEFAULT_CONTEXT_PRESET
    return preset


def current() -> AgentSettings:
    raw = _read_raw()
    overrides = _valid_overrides(raw)
    values = {spec.key: overrides.get(spec.key, default_of(spec)) for spec in PARAMS}
    return AgentSettings(**values, context_preset=_preset_of(raw), overrides=overrides)


def update(changes: dict[str, Any], *, context_preset: str | None = None) -> AgentSettings:
    """部分更新：校验全部通过才写入；值等于默认时删除覆盖项（不留无意义的覆盖）。"""
    validated: dict[str, Any] = {}
    for key, value in changes.items():
        spec = PARAMS_BY_KEY.get(key)
        if spec is None:
            raise ValueError(f"未知设置项：{key}")
        validated[key] = validate_value(spec, value)
    if context_preset is not None and context_preset not in CONTEXT_PRESETS:
        raise ValueError(f"上下文档位只能是 {' / '.join(CONTEXT_PRESETS)}")
    with _lock:
        raw = dict(_read_raw())
        stored = dict(raw.get("overrides") or {}) if isinstance(raw.get("overrides"), dict) else {}
        for key, value in validated.items():
            if value == default_of(PARAMS_BY_KEY[key]):
                stored.pop(key, None)
            else:
                stored[key] = value
        raw.update(version=FILE_VERSION, overrides=stored)
        if context_preset is not None:
            raw["context_preset"] = context_preset
        _write_raw(raw)
    return current()


def reset(key: str | None = None) -> AgentSettings:
    """恢复默认：给 key 只删该项；不给则清空全部覆盖并把档位设回均衡（未知键保留）。"""
    if key is not None and key not in PARAMS_BY_KEY and key != "context_preset":
        raise ValueError(f"未知设置项：{key}")
    with _lock:
        raw = dict(_read_raw())
        stored = dict(raw.get("overrides") or {}) if isinstance(raw.get("overrides"), dict) else {}
        if key is None:
            stored = {k: v for k, v in stored.items() if k not in PARAMS_BY_KEY}
            raw["context_preset"] = DEFAULT_CONTEXT_PRESET
        elif key == "context_preset":
            raw["context_preset"] = DEFAULT_CONTEXT_PRESET
        else:
            stored.pop(key, None)
        raw.update(version=FILE_VERSION, overrides=stored)
        _write_raw(raw)
    return current()
