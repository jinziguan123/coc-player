"""审计器必须识别间接环、相对导入和局部导入，不能把导入子模块误判为包回边。"""
import importlib.util
from pathlib import Path


spec = importlib.util.spec_from_file_location(
    "audit_import_graph", Path(__file__).resolve().parents[2] / "scripts" / "audit_import_graph.py",
)
audit_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_module)


def test_静态扫描识别三节点环并避免包导入假环(tmp_path):
    root = tmp_path / "app"
    root.mkdir()
    sources = {
        "__init__.py": "from . import a\n",
        "a.py": "from .b import value\n",
        "b.py": "def run():\n    from app import c\n",
        "c.py": "import app.a\n",
    }
    for name, source in sources.items():
        (root / name).write_text(source)
    result = audit_module.audit(root)
    assert result["modules"] == 4
    assert len(result["components"]) == 1
    component = result["components"][0]
    assert component["modules"] == ["app.a", "app.b", "app.c"]
    assert [(e["from"], e["to"], e["line"]) for e in component["cycle"]] == [
        ("app.a", "app.b", 1), ("app.b", "app.c", 2), ("app.c", "app.a", 1),
    ]
