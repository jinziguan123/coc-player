"""组件（含第三方插件）给 KP 新增的工具：声明 + 执行上下文 + 返回值。

内置 KP 工具（检定、扣血、换场景……）在 ``app.ai.tools`` 的注册表里，带确定性结算，不可覆盖；
这里是另一条口子——组件在 ``Component(kp_tools=...)`` 里声明自己的工具，**随组件开关进出**：
房主在规则页打开该组件，本局的 KP 才看得到这些工具（原生工具 schema 与工具模式说明里都有）。

一个工具 = 名字 + 给模型看的说明 + 参数 JSON Schema + 执行函数。执行函数拿到 :class:`KpToolContext`
与模型给的参数（已解析成 dict），返回给模型的结果文本（``str``），或 :class:`KpToolResult`
（需要本轮生成就此收束时置 ``suspend``）。同步、异步函数都可以。

执行函数抛异常不会中断回合：KP 会收到「工具执行出错，请直接继续叙述」，异常写进日志。
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

#: 工具名的写法：小写 snake_case，与内置工具同一风格，也是 OpenAI function name 的安全子集。
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,47}$")


@dataclass(frozen=True)
class KpToolContext:
    """执行一次工具调用时可用的运行期上下文。

    ``db`` 是本轮的数据库会话：写库后要自己 ``commit``。``ruleset`` 是本局生效规则，
    组件参数从 ``ruleset.options`` 上读（如 ``ruleset.options.my_param``）。
    """

    db: Any
    session_id: str
    game_session: Any
    module: Any
    player_char: Any
    teammates: tuple[Any, ...]
    ruleset: Any


@dataclass(frozen=True)
class KpToolResult:
    """工具执行结果：``text`` 回注给模型；``suspend=True`` 时本轮生成就此收束。"""

    text: str
    suspend: bool = False


@dataclass(frozen=True)
class KpTool:
    """组件声明的一个 KP 工具。"""

    name: str
    #: 给模型看的说明：什么时候用、什么时候别用。写得像内置工具那样具体，模型才会用对。
    description: str
    #: 参数的 JSON Schema（``{"type": "object", "properties": {...}, "required": [...]}``）。
    parameters: dict
    execute: Callable[[KpToolContext, dict], str | KpToolResult | Awaitable[str | KpToolResult]]


def check_tool(tool: Any) -> list[str]:
    """一个工具声明本身的问题（不涉及与其他工具重名）。"""
    if not isinstance(tool, KpTool):
        return [f"kp_tools 里的 {tool!r} 不是 KpTool"]
    errors: list[str] = []
    if not NAME_PATTERN.match(tool.name):
        errors.append(f"KP 工具名「{tool.name}」应为小写字母开头的 snake_case（2~48 个字符）")
    if not (tool.description or "").strip():
        errors.append(f"KP 工具 {tool.name} 缺少说明（description）")
    params = tool.parameters
    if not isinstance(params, dict) or params.get("type") != "object":
        errors.append(f"KP 工具 {tool.name} 的 parameters 应为 type=object 的 JSON Schema")
    if not callable(tool.execute):
        errors.append(f"KP 工具 {tool.name} 的 execute 不可调用")
    return errors
