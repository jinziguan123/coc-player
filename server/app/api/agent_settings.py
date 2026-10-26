"""agent 高级设置接口：运行预算与上下文档位（仅房主本机）。"""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import agent_settings
from app.ai.context import context_preset_values
from app.api.deps import require_local_client

# 只限本机：这些参数直接决定房主的模型花费与回合时长，客人不该能改。
router = APIRouter(
    prefix="/api/settings", tags=["settings"],
    dependencies=[Depends(require_local_client)],
)


class AgentParam(BaseModel):
    key: str
    label: str
    description: str
    kind: str
    value: Any
    default: Any
    overridden: bool
    minimum: float | None = None
    maximum: float | None = None
    choices: list[str] = []
    unit: str = ""


class ContextPresetInfo(BaseModel):
    key: str
    budget_ceiling: int
    min_recent_events: int
    max_summary_tokens: int
    visited_scene_window: int


class AgentSettingsView(BaseModel):
    params: list[AgentParam]
    context_preset: str
    default_context_preset: str
    context_presets: list[ContextPresetInfo]


class AgentSettingsUpdate(BaseModel):
    values: dict[str, Any] = {}
    context_preset: str | None = None


def _view(current: agent_settings.AgentSettings) -> AgentSettingsView:
    return AgentSettingsView(
        params=[
            AgentParam(
                key=spec.key, label=spec.label, description=spec.description, kind=spec.kind,
                value=getattr(current, spec.key), default=agent_settings.default_of(spec),
                overridden=spec.key in current.overrides, minimum=spec.minimum,
                maximum=spec.maximum, choices=list(spec.choices), unit=spec.unit,
            )
            for spec in agent_settings.PARAMS
        ],
        context_preset=current.context_preset,
        default_context_preset=agent_settings.DEFAULT_CONTEXT_PRESET,
        context_presets=[
            ContextPresetInfo(key=name, **vars(context_preset_values(name)))
            for name in agent_settings.CONTEXT_PRESETS
        ],
    )


@router.get("/agent", response_model=AgentSettingsView)
def get_agent_settings() -> AgentSettingsView:
    return _view(agent_settings.current())


@router.put("/agent", response_model=AgentSettingsView)
def update_agent_settings(data: AgentSettingsUpdate) -> AgentSettingsView:
    try:
        current = agent_settings.update(data.values, context_preset=data.context_preset)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return _view(current)


@router.delete("/agent", response_model=AgentSettingsView)
def reset_agent_settings(key: str | None = None) -> AgentSettingsView:
    """恢复默认：带 ``key`` 只恢复该项（含 ``context_preset``），不带则全部恢复。"""
    try:
        current = agent_settings.reset(key)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return _view(current)
