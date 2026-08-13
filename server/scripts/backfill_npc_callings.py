"""给**存量**模组的 NPC 补 aliases / 修 unknown_as。

新导入的模组已经在解析提示词里带上这两个字段；这个脚本是给之前导进来的那些补的。

    cd server && .venv/bin/python scripts/backfill_npc_callings.py            # 只看，不改
    cd server && .venv/bin/python scripts/backfill_npc_callings.py --apply    # 确认后落库

补的是什么：

- **aliases**：场上对这个角色的其它叫法（史蒂芬·诺特 →「诺特先生」）。档案存全名而 KP
  只写「诺特先生」，两者对不上，界面就只能管委托人叫「陌生男性」。
- **unknown_as**：玩家还不知道它叫什么时界面上的称呼。**默认只补空缺**，已经有值的不动。
  实测重写一遍有得有失：「不死恶魔」这种揭底的说法确实改好了，但「鼠群」被改成「不明存在」
  （老鼠一眼就看得出是老鼠）、性别没写明的青年被猜成「陌生男性」（原值「陌生的青年」恰恰
  是不猜）。要连已有值一起重写，加 --rewrite-unknown-as，并逐条看过预览再落库。

默认**只预览**：逐本打印改前改后。重点自己过一眼两件事——别名里有没有混进地名/建筑名
（「科比特」是房子的名字，混进去 KP 一提老宅就等于报出了住在里面的东西），
以及新填的 unknown_as 有没有泄底。加 --apply 才写库。
"""

from __future__ import annotations

import asyncio
import copy
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models.module import Module  # noqa: E402
from app.services import module_service  # noqa: E402


def _show(before: dict, after: dict) -> bool:
    """打印单个 NPC 的改动；没改返回 False。"""
    name = after.get("name") or after.get("id")
    old_alias = list(before.get("aliases") or [])
    new_alias = list(after.get("aliases") or [])
    old_unknown = str(before.get("unknown_as") or "")
    new_unknown = str(after.get("unknown_as") or "")
    if old_alias == new_alias and old_unknown == new_unknown:
        return False
    print(f"    {name}")
    if old_alias != new_alias:
        print(f"      别名：{old_alias or '（无）'} → {new_alias or '（无）'}")
    if old_unknown != new_unknown:
        print(f"      未识别时称呼：{old_unknown or '（自动推断）'} → {new_unknown}")
    return True


async def main(apply: bool, rewrite: bool) -> int:
    db = SessionLocal()
    modules = db.query(Module).all()
    print(
        f"共 {len(modules)} 本模组；模式：{'落库' if apply else '仅预览'}；"
        f"未识别称呼：{'连已有值一起重写' if rewrite else '只补空缺'}\n"
    )
    touched = 0

    for m in modules:
        npcs = [n for n in (m.npcs or []) if isinstance(n, dict)]
        print(f"— {m.title}（{len(npcs)} 个 NPC）")
        if not npcs:
            print("  （无 NPC，跳过）")
            continue
        before = copy.deepcopy(npcs)
        scene_titles = [
            str(s.get("title") or s.get("name") or "")
            for s in (m.scenes or []) if isinstance(s, dict)
        ]
        after = await module_service.generate_npc_callings(
            copy.deepcopy(npcs), scene_titles, m.title or "", m.rule_system or "coc",
            rewrite_unknown_as=rewrite,
        )
        changed = False
        for b, a in zip(before, after, strict=True):
            changed |= _show(b, a)
        if not changed:
            print("  （无改动）")
            continue
        touched += 1
        if apply:
            m.npcs = after        # JSON 列必须整体重赋值才会脏
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
    do_rewrite = "--rewrite-unknown-as" in sys.argv
    if do_apply:
        # 改的是玩家可见称呼、不可逆，先备份整库（与其它存量修订脚本同一做法）
        src = Path(settings.db_path)
        bak = src.with_suffix(src.suffix + ".bak-callings")
        shutil.copy2(src, bak)
        print(f"已备份数据库到 {bak}\n")
    raise SystemExit(asyncio.run(main(do_apply, do_rewrite)))
