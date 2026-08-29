"""DESIGN.md 的架构图必须与生成脚本一致。

图是 `scripts/gen_design_diagrams.py` 的产物。改了脚本却忘了重新生成，文档里就会挂着一张
过期的图——而图比文字更容易被当成事实，也更没人会去核对。这条和 OpenAPI 的
`git diff --exit-code` 是同一个套路：**生成物不允许手改，也不允许落后于生成器**。

放在后端测试目录纯粹因为这是仓库里唯一跑 pytest 的地方；它检查的是仓库根的文档产物。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "gen_design_diagrams.py"
IMAGES = ROOT / "docs" / "images"


def _load():
    spec = importlib.util.spec_from_file_location("gen_design_diagrams", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["gen_design_diagrams"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def generated() -> dict[str, str]:
    mod = _load()
    builders = (mod.deployment, mod.turn_pipeline, mod.turn_phases, mod.context_injection)
    return dict(build() for build in builders)


def test_磁盘上的图与生成脚本一致(generated):
    stale = []
    for name, svg in generated.items():
        path = IMAGES / name
        if not path.exists():
            stale.append(f"{name}：文件不存在")
        elif path.read_text("utf-8") != svg:
            stale.append(f"{name}：内容与脚本产物不一致")
    assert not stale, (
        "架构图落后于生成脚本，或被手改过。跑 `python3 scripts/gen_design_diagrams.py` 重新生成"
        "（不要直接编辑 SVG——下次重生成会把手改覆盖掉）。\n" + "\n".join(stale)
    )


def test_DESIGN_引用的图都存在且没有残留的_mermaid():
    design = (ROOT / "DESIGN.md").read_text("utf-8")
    assert "```mermaid" not in design, "DESIGN.md 里又出现了 mermaid 块——架构图统一走生成的 SVG"

    import re
    refs = re.findall(r'!\[[^\]]*\]\((docs/images/[^)]+)\)', design)
    assert refs, "DESIGN.md 里一张架构图都没引用，这条断言退化成了空转"
    missing = [r for r in refs if not (ROOT / r).exists()]
    assert not missing, f"DESIGN.md 引用了不存在的图：{missing}"


def test_生成的图都被文档引用到(generated):
    """反向检查：生成了却没人引用的图是孤儿，要么补引用、要么从脚本里删掉。"""
    design = (ROOT / "DESIGN.md").read_text("utf-8")
    orphans = [n for n in generated if f"docs/images/{n}" not in design]
    assert not orphans, f"这些图没有被 DESIGN.md 引用：{orphans}"
