"""给**存量**模组补跑一遍防剧透自检（简介 / 世界观导入 / 开场钩子）。

新导入的模组已经在解析流程里自动过这一步；这个脚本是给之前导进来的那些补的。

    cd server && .venv/bin/python scripts/redact_existing_modules.py            # 只看，不改
    cd server && .venv/bin/python scripts/redact_existing_modules.py --apply    # 确认后落库

默认**只预览**：逐本打印改前改后，让你自己判断有没有把该留的东西删掉（年代、地点、基调、
内容警示、玩家的身份与委托都不是剧透，必须留下）。加 --apply 才写库。
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models.module import Module  # noqa: E402
from app.services import module_service  # noqa: E402


def _show(tag: str, before: str, after: str) -> bool:
    if (before or "").strip() == (after or "").strip():
        return False
    print(f"  【{tag}】")
    print(f"    改前：{before}")
    print(f"    改后：{after}")
    return True


async def main(apply: bool) -> int:
    db = SessionLocal()
    modules = db.query(Module).all()
    print(f"共 {len(modules)} 本模组；模式：{'落库' if apply else '仅预览'}\n")
    touched = 0

    for m in modules:
        ws = dict(m.world_setting or {})
        parsed = {
            "description": m.description or "",
            "intro": ws.get("intro") or "",
            "player_brief": ws.get("player_brief") or "",
            "truth": m.truth or "",
            "npcs": m.npcs or [],
            "clues": m.clues or [],
            "world_setting": ws,
        }
        if not parsed["truth"].strip():
            print(f"— {m.title}：无幕后真相，跳过（无从判断泄漏）")
            continue

        before = (parsed["description"], parsed["intro"], parsed["player_brief"])
        out = await module_service.redact_player_facing(parsed, m.rule_system or "coc")
        changed = False
        print(f"— {m.title}")
        for tag, b, key in (
            ("简介", before[0], "description"),
            ("世界观导入", before[1], "intro"),
            ("开场钩子", before[2], "player_brief"),
        ):
            changed |= _show(tag, b, out.get(key) or "")
        if not changed:
            print("  （无泄漏）")
            continue
        touched += 1
        if apply:
            m.description = out["description"]
            new_ws = dict(ws)
            new_ws["intro"] = out["intro"]
            new_ws["player_brief"] = out["player_brief"]
            m.world_setting = new_ws      # JSON 列必须整体重赋值才会脏
            db.add(m)

    if apply and touched:
        db.commit()
        print(f"\n已落库：{touched} 本")
    elif touched:
        print(f"\n{touched} 本有改动。确认无误后加 --apply 落库。")
    else:
        print("\n没有需要改的。")
    return 0


if __name__ == "__main__":
    do_apply = "--apply" in sys.argv
    if do_apply:
        # 改的是玩家可见文案、不可逆，先备份整库（与清理脏数据时同一做法）
        src = Path(settings.db_path)
        bak = src.with_suffix(src.suffix + ".bak-redact")
        shutil.copy2(src, bak)
        print(f"已备份数据库到 {bak}\n")
    raise SystemExit(asyncio.run(main(do_apply)))
