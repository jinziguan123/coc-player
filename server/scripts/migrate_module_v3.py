"""把存量模组升级到 v3（事件—条件—效果）结构。

默认 **dry-run**，只打印每个模组会被改动的部分；加 `--apply` 才写库。

    cd server && .venv/bin/python scripts/migrate_module_v3.py
    cd server && .venv/bin/python scripts/migrate_module_v3.py --apply

做四件事：
1. scenes 里 kind=event 的条目提升到 events 并从地点列表移除；
2. world_setting.start 缺失时取第一个地点；
3. endings.when 字符串包成 {text, expr: null}；
4. 重算 validation、写 schema_version=3。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

from app.database import SessionLocal  # noqa: E402
from app.models.module import Module  # noqa: E402
from app.services import module_flow  # noqa: E402


def _payload(module: Module) -> dict:
    return {
        "world": module.world_setting or {},
        "scenes": module.scenes or [],
        "events": module.events or [],
        "npcs": module.npcs or [],
        "clues": module.clues or [],
        "triggers": module.triggers or [],
        "endings": module.endings or [],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="真正写库（默认只打印）")
    args = parser.parse_args()

    db = SessionLocal()
    changed = 0
    try:
        for module in db.query(Module).all():
            upgraded = module_flow.upgrade_legacy_module(_payload(module))
            events = upgraded["events"]
            scenes = upgraded["scenes"]
            report = upgraded["validation"]
            before = (len(module.scenes or []), len(module.events or []), module.schema_version)
            after = (len(scenes), len(events), upgraded["schema_version"])
            if before == after and not args.apply:
                continue
            changed += 1
            print(
                f"{module.title}（{module.id}）：场景 {before[0]}->{after[0]}，"
                f"事件 {before[1]}->{after[1]}，schema {before[2]}->{after[2]}，"
                f"warnings {len(report.get('warnings') or [])}，errors {len(report.get('errors') or [])}"
            )
            if not args.apply:
                continue
            module.world_setting = upgraded["world"]
            module.scenes = scenes
            module.events = events
            module.endings = upgraded["endings"]
            module.validation = report
            module.schema_version = upgraded["schema_version"]
        if args.apply:
            db.commit()
    finally:
        db.close()
    mode = "已写入" if args.apply else "dry-run（加 --apply 写库）"
    print(f"{mode}：{changed} 个模组需要/已升级")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
