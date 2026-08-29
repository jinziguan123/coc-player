"""重新生成 ``fixtures/kp_context_golden.json``。

    cd server && .venv/bin/python -m tests.regen_kp_context_golden

**只在有意改变上下文装配时才跑它**，跑完务必 ``git diff`` 逐条看快照差异——
这组快照的全部价值就在于「没打算改的小节不该动」。
"""

from __future__ import annotations

import json
from pathlib import Path

from tests._kp_context_corpus import CASES, render


def main() -> None:
    snapshot = {name: render(kwargs) for name, kwargs in CASES}
    out = Path(__file__).parent / "fixtures" / "kp_context_golden.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1) + "\n", "utf-8")
    print(f"已写入 {out}（{len(snapshot)} 个用例）")


if __name__ == "__main__":
    main()
