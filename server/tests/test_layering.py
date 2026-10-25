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


_PURE_APPLICATION = [
    "movement_execution.py", "movement_state.py", "movement_adjudication.py", "movement_settlement.py",
]


@pytest.mark.parametrize("filename", _PURE_APPLICATION)
def test_移动提交与状态边界不依赖数据库或外部服务(filename):
    import sys

    pure = {f"app.application.{name.removesuffix('.py')}" for name in _PURE_APPLICATION}
    path = APP / "application" / filename
    tree = ast.parse(path.read_text("utf-8"))
    for mod, _line, _local in _imports(tree):
        # 应用边界可以依赖领域规则（依赖方向向内）和彼此，但不得碰 ORM、服务层或 AI。
        assert (mod.split(".")[0] in sys.stdlib_module_names or mod.startswith("app.domain.")
                or mod in pure), mod
    assert not any(isinstance(node, ast.ImportFrom) and node.level for node in ast.walk(tree))

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


def test_领域核心只依赖标准库与领域模块():
    """独立于旧 services 分层：禁止领域重新绑回 ORM、AI 或应用用例。"""
    import sys

    domain = APP / "domain"
    assert domain.is_dir()
    paths = list(domain.rglob("*.py"))
    assert any(p.name != "__init__.py" for p in paths)
    for path in paths:
        tree = ast.parse(path.read_text("utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level:
                package_depth = len(path.parent.relative_to(domain).parts)
                assert node.level <= package_depth + 1, f"{path} 相对导入越过领域边界"
        for mod, line, _local in _imports(tree):
            assert (
                mod.split(".")[0] in sys.stdlib_module_names
                or mod == "app.domain" or mod.startswith("app.domain.")
            ), f"{path}:{line} 领域不得依赖 {mod}"


def test_润色器不能反向依赖回合上下文且位置变换仅依赖标准库():
    import sys

    transform = ast.parse((APP / "application" / "narration_marks.py").read_text("utf-8"))
    assert all(mod.split(".")[0] in sys.stdlib_module_names for mod, _, _ in _imports(transform))
    assert not any(isinstance(n, ast.ImportFrom) and n.level for n in ast.walk(transform))
    editor = ast.parse((APP / "services" / "style_editor.py").read_text("utf-8"))
    targets = []
    for node in ast.walk(editor):
        if isinstance(node, ast.Import):
            targets.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            targets.append(node.module or "")
            targets.extend(f"{node.module}.{a.name}" for a in node.names)
    assert not any(t == "app.services.turn_context" or t.startswith("app.services.turn_context.")
                   for t in targets)


def test_移动服务不依赖检定门控_避免两者互相导入():
    """技能匹配规则在领域层；check_gating 在回合开始时调用 movement，反方向不得再出现。"""
    tree = ast.parse((APP / "services" / "movement.py").read_text("utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            targets = [node.module, *(f"{node.module}.{a.name}" for a in node.names)]
        else:
            continue
        assert "app.services.check_gating" not in targets, f"movement.py:{node.lineno} 不得依赖 check_gating"


def _import_targets(tree: ast.AST) -> list[tuple[str, int]]:
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [(a.name, node.lineno) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.append((node.module, node.lineno))
            out += [(f"{node.module}.{a.name}", node.lineno) for a in node.names]
    return out


def test_移动服务不回调计划效果_补挂判断由调用方注入():
    """planned_effects 调用 movement；movement 若再反向导入 planned_effects 就又成了互相导入。"""
    tree = ast.parse((APP / "services" / "movement.py").read_text("utf-8"))
    for mod, line in _import_targets(tree):
        assert mod != "app.services.planned_effects", f"movement.py:{line} 不得依赖 planned_effects"


#: 移动状态的存档键只归移动模块与其状态边界解释；其他模块经 movement 的查询函数读取。
_MOVEMENT_STATE_OWNERS = {APP / "services" / "movement.py", APP / "application" / "movement_state.py"}


def test_移动回合状态的存档键只由移动模块解释():
    offenders = []
    for path in sorted(APP.rglob("*.py")):
        if path in _MOVEMENT_STATE_OWNERS:
            continue
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if isinstance(node, ast.Constant) and node.value in ("turn_moves", "pending_moves"):
                offenders.append(f"{path.relative_to(APP)}:{node.lineno} 字面量 {node.value}")
            if isinstance(node, ast.Attribute) and node.attr in ("PENDING_KEY", "MOVES_KEY", "turn_records") \
                    and isinstance(node.value, ast.Name) and node.value.id == "movement":
                offenders.append(f"{path.relative_to(APP)}:{node.lineno} movement.{node.attr}")
    assert not offenders, "\n".join(offenders)


def test_计划效果只读自动检定状态_不反向调用门控编排():
    tree = ast.parse((APP / "services" / "planned_effects.py").read_text("utf-8"))
    assert all(not (mod == "app.services.check_gating" or mod.startswith("app.services.check_gating."))
               for mod, _ in _import_targets(tree))


def test_自动检定状态不驱动收益或回合编排():
    tree = ast.parse((APP / "services" / "auto_check_state.py").read_text("utf-8"))
    assert all(not mod.startswith("app.services") for mod, _ in _import_targets(tree))


def test_上下文模块不依赖位置写入入口():
    tree = ast.parse((APP / "services" / "turn_context.py").read_text("utf-8"))
    for mod, _ in _import_targets(tree):
        assert mod not in {"app.services.movement", "app.services.turn_travel"}
    assert not any(isinstance(n, ast.Attribute) and n.attr in {"set_char_location", "update_scene"}
                   for n in ast.walk(tree))


def test_规则配置不反向依赖会话服务_队员查询不依赖其他服务():
    tree = ast.parse((APP / "services" / "rule_options_service.py").read_text("utf-8"))
    assert all(not (mod == "app.services.session_service" or mod.startswith("app.services.session_service."))
               for mod, _ in _import_targets(tree))
    tree = ast.parse((APP / "services" / "party_queries.py").read_text("utf-8"))
    assert all(not mod.startswith("app.services") for mod, _ in _import_targets(tree))


def test_收益结算只依赖状态与存储接口_不回调规划或回合编排():
    path = APP / "services" / "reward_settlement.py"
    allowed = {"auto_check_state", "effect_channel", "event_store", "event_protocol",
               "inventory_service", "world_memory"}
    for mod, line in _import_targets(ast.parse(path.read_text("utf-8"))):
        assert not mod.startswith("app.ai"), f"{path}:{line} 不得依赖 AI"
        if mod.startswith("app.services."):
            assert mod.split(".")[2] in allowed, f"{path}:{line} 收益边界不得依赖 {mod}"
    for name in ("check_gating", "human_kp_desk"):
        tree = ast.parse((APP / "services" / f"{name}.py").read_text("utf-8"))
        assert all(mod != "app.services.planned_effects" for mod, _ in _import_targets(tree))
