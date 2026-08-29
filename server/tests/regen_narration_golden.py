"""重新生成 ``fixtures/narration_golden.json``。

    cd server && .venv/bin/python -m tests.regen_narration_golden

**只在有意改变 filter_narration_stream 行为时才跑它**，跑完务必 ``git diff`` 逐条看
快照差异——这组快照的全部价值就在于「没打算改的地方不该动」。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from tests._narration_corpus import CASES, run as _run


async def main() -> None:
    snapshot = {name: await _run(text, kwargs) for name, text, kwargs in CASES}
    out = Path(__file__).parent / "fixtures" / "narration_golden.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1) + "\n", "utf-8")
    print(f"已写入 {out}（{len(snapshot)} 个用例）")


if __name__ == "__main__":
    asyncio.run(main())
