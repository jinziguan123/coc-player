"""输出 Python 模块静态导入图的强连通分量和一条带行号的循环证据。

包含包初始化文件、局部及相对导入；不执行模块，不展开动态导入或兼容导出符号的归属。
包初始化代码有自己的节点；不会将父包的隐式执行展开为每条边，以免产生虚假的回边。
"""
from __future__ import annotations

import ast
import json
from pathlib import Path


def audit(root: Path) -> dict:
    files = {}
    for path in sorted(root.rglob("*.py")):
        parts = path.relative_to(root.parent).with_suffix("").parts
        name = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
        files[name] = path
    edges = {name: {} for name in files}
    for name, path in files.items():
        package = name if path.name == "__init__.py" else name.rpartition(".")[0]
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            targets = []
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    parts = package.split(".")
                    base = ".".join(parts[:len(parts) - node.level + 1] + ([base] if base else []))
                targets = [f"{base}.{alias.name}" if f"{base}.{alias.name}" in files else base
                           for alias in node.names]
            for target in targets:
                if target in files and target != name:
                    edges[name].setdefault(target, node.lineno)

    indices, low, stack, active, components = {}, {}, [], set(), []

    def visit(name):
        indices[name] = low[name] = len(indices)
        stack.append(name)
        active.add(name)
        for target in edges[name]:
            if target not in indices:
                visit(target)
                low[name] = min(low[name], low[target])
            elif target in active:
                low[name] = min(low[name], indices[target])
        if low[name] == indices[name]:
            component = []
            while True:
                target = stack.pop()
                active.remove(target)
                component.append(target)
                if target == name:
                    break
            if len(component) > 1:
                components.append(sorted(component))

    for name in files:
        if name not in indices:
            visit(name)

    def witness(component):
        allowed, path, visiting = set(component), [], set()
        def walk(name):
            path.append(name)
            visiting.add(name)
            for target in edges[name]:
                if target not in allowed:
                    continue
                if target in visiting:
                    return path[path.index(target):] + [target]
                found = walk(target)
                if found:
                    return found
            path.pop()
            visiting.remove(name)
            return None
        cycle = walk(component[0])
        return [{"from": a, "to": b, "file": str(files[a].relative_to(root.parent)),
                 "line": edges[a][b]} for a, b in zip(cycle, cycle[1:])]

    return {"modules": len(files), "edges": sum(map(len, edges.values())),
            "components": [{"size": len(c), "modules": c, "cycle": witness(c)}
                           for c in sorted(components, key=lambda c: (-len(c), c))]}


if __name__ == "__main__":
    print(json.dumps(audit(Path(__file__).resolve().parents[1] / "server" / "app"),
                     ensure_ascii=False, indent=2))
