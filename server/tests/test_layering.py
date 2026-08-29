"""依赖方向的**结构性约束**：谁可以 import 谁。

DESIGN §4.10 的可执行形式。为什么需要它：这个项目曾在 ``ai/`` 下留着六处
``# 局部导入避免顶层循环依赖`` 的注释，而那个循环**根本不存在**——注释比代码旧了几个月，
没人知道，因为没有任何东西检查方向。判断写在文档里会腐烂，写成测试不会。

分层（自上而下，只许向下依赖）::

    api/                 HTTP 端点、参数校验、授权
      ↓
    services/（编排）     turn_orchestrator / kp_tool_loop … —— 会 import ai/
      ↓
    ai/                  Provider 抽象、上下文装配、规划器、子代理
      ↓
    services/（领域）     session_service / world_memory / hex_map … —— 不碰 ai/
      ↓
    models/ · rules/     ORM 与规则引擎

注意 ``services/`` 里其实住着**两层**：会 import ``ai/`` 的是编排服务，不 import 的是
领域服务，``ai/`` 夹在两者中间。这正是「看起来成环」的来源——它不是环，是一个目录装了
两层。真要消除歧义得把目录拆开，那是更大的动作；在那之前，本测试守住能机械判定的部分。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent / "app"

#: 层 → 它**不得**依赖的层。反向依赖一律是分层倒置，不接受个案豁免——
#: 真需要跨层拿东西，是那个东西放错了地方（例：AI 配置曾住在 api/ai_settings，
#: 于是 services 和 ai 都得反过来 import api，最后抽成了 ai/profile_store）。
FORBIDDEN: dict[str, set[str]] = {
    "ai": {"api"},
    "services": {"api"},
    "models": {"services", "ai", "api"},
    "rules": {"services", "ai", "api"},
    "schemas": {"services", "ai", "api"},
}


def _imports(tree: ast.AST):
    """产出 (被导入模块, 行号, 是否局部导入)。局部导入照查——它绕不过这条规则。"""
    scoped = {
        id(node)
        for fn in ast.walk(tree)
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
        for node in ast.walk(fn)
    }
    for node in ast.walk(tree):
        local = id(node) in scoped
        if isinstance(node, ast.ImportFrom) and node.module:
            yield node.module, node.lineno, local
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, node.lineno, local


def _violations() -> list[str]:
    out: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        layer = path.relative_to(APP).parts[0]
        forbidden = FORBIDDEN.get(layer)
        if not forbidden:
            continue
        tree = ast.parse(path.read_text("utf-8"))
        for mod, lineno, local in _imports(tree):
            parts = mod.split(".")
            if len(parts) < 2 or parts[0] != "app" or parts[1] not in forbidden:
                continue
            where = "（局部导入）" if local else ""
            out.append(f"{path.relative_to(APP.parent)}:{lineno} {layer} → {parts[1]}{where}：{mod}")
    return out


def test_依赖方向没有倒置():
    violations = _violations()
    assert not violations, (
        "出现反向依赖。别用局部导入绕过——那只会把问题藏起来（本项目上一次就是这么藏了几个月）。"
        "正确做法是把被跨层引用的东西挪到它该在的层。\n" + "\n".join(violations)
    )


def test_ai_层不再留循环依赖的局部导入():
    """``ai/`` 对 ``services/`` 的依赖一律走顶层导入。

    局部导入本身不是错（``usage_tracker`` 里那处 ``SessionLocal`` 就有正当理由：
    顶层引 ``app.database`` 会让模块在导入期绑定引擎，影响测试替换）。错的是**拿它当
    「循环依赖」的创可贴**——真有环该拆层，没有环就别留误导后人的注释。
    """
    offenders = []
    for path in sorted((APP / "ai").rglob("*.py")):
        tree = ast.parse(path.read_text("utf-8"))
        for mod, lineno, local in _imports(tree):
            if local and mod.startswith("app.services"):
                offenders.append(f"{path.relative_to(APP.parent)}:{lineno} {mod}")
    assert not offenders, (
        "ai/ 里又出现了对 services 的局部导入。若确因导入期副作用而必须局部导入，"
        "请在此处加豁免并写清理由；若是为了「避免循环依赖」，先确认那个环真的存在。\n"
        + "\n".join(offenders)
    )


@pytest.mark.parametrize("layer", sorted(FORBIDDEN))
def test_每个受约束的层都确实存在(layer):
    """防止目录改名后规则静默失效——层没了，上面两条就变成了空转。"""
    assert (APP / layer).is_dir(), f"app/{layer}/ 不存在，FORBIDDEN 该更新了"
