"""一次性数据回填：把历史存档里已经给过的线索 / 已经演过的场景机制点补进世界记忆。

背景
----
线索台账（``world_state.clue_ledger``）此前唯一的写入口是规划器填的
``clue_policy.candidate_clue_ids``——记账这件事本身也交给了 LLM，它没把「祠堂里那块
石板」认回 ``clue_3``，账就永远不记。实测『闇暗山』那局跑了 6 个场景、187 条叙述，
台账一条没有，KP 于是对着线索明文重演，玩家当场吐槽「这不就是你刚才和我说的内容嘛」。

叙事进度记账（``planned_effects.record_narrated_progress``）已经补上，但它只看**本轮新
叙事**，存量存档追不回来：那些早就给过的线索仍显示「尚未给出」。本脚本重放历史事件补记。

回填规则（保守且幂等）
----------------------
* 逐条重放 ``narration`` / ``dialogue`` 事件，用与运行时**同一套**匹配函数
  （``_clue_shown_in_narration`` / ``_scene_event_narrated``），不另立一套判据。
* 场景归属取事件自己的 ``metadata.scene_id``——每条事件都带，历史位置可精确还原，
  不必拿「当前场景」去套整局（那会把 A 场景的线索记到 B 场景的叙事上）。
* 线索一律只记 ``partial``（有所察觉），与运行时兜底同档：文本匹配必有误差，宁可让 KP
  觉得玩家摸到了边角而继续深入，也不能凭一次误匹配把线索判成已掌握、从此不再揭示。
* 已有的台账条目一概不动（``known`` 不降级、``seq`` 不改写），所以重复运行不再产生改动。
* ``seq`` 记命中那条事件的真实序号，``discovered_by`` 记该局全部玩家角色——分头行动的
  历史状态已无从还原，这一项偏宽松，但它只影响「谁知道」的展示，不影响「别重复给」的判断。

为什么默认不回填场景机制点
--------------------------
``_scene_event_narrated`` 是「trigger 的实词命中一个即算」，运行时只对文刚生成的那一轮，
误差有限；回填要对文整局几百条叙事，同一个词迟早会撞上。实测『闇暗山』seq 364 那句
「混着一记极轻的拖拽响动」（远处的声响）就命中了机制点「进入最里面的小屋被拖拽」，
『常暗之箱』里一条事件同时命中了 2 号车厢的三条机制点。

而机制点标错的代价是**不对称**的：标成「已发生」会让 KP 跳过一个根本没演的桥段，
玩家直接错过内容，比重演一次更糟。线索的判据是「名字 + 动作证据近距离同现」双条件，
实测抽查准确，且误差只落在 partial（有所察觉）这一档，不会屏蔽后续揭示——故默认只回填线索。
确实想要机制点回填时加 ``--scene-events``，回填后自己核一遍。

用法
----
    .venv/bin/python scripts/backfill_world_memory.py                    # 预演，只打印
    .venv/bin/python scripts/backfill_world_memory.py --apply           # 实际写入
    .venv/bin/python scripts/backfill_world_memory.py --session <id>    # 只跑一局
    .venv/bin/python scripts/backfill_world_memory.py --scene-events    # 连机制点一起（慎用）
    .venv/bin/python scripts/backfill_world_memory.py --db /path/trpg.db
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine                              # noqa: E402
from sqlalchemy.orm import sessionmaker                           # noqa: E402

from app.models.base import Base                                  # noqa: E402,F401
from app.models.character import Character                        # noqa: E402,F401
from app.models.event_log import EventLog                         # noqa: E402
from app.models.module import Module                              # noqa: E402
from app.models.session import GameSession                        # noqa: E402
from app.models.session_participant import SessionParticipant     # noqa: E402,F401
from app.services import world_memory                             # noqa: E402
from app.services.planned_effects import (                        # noqa: E402
    _clue_shown_in_narration,
    _scene_event_narrated,
)

DEFAULT_DB = Path(__file__).resolve().parent.parent / "trpg.db"


def _party_ids(db, session: GameSession) -> list[str]:
    """该局的玩家角色 id（含主角），供 discovered_by 使用。"""
    ids = []
    for p in db.query(SessionParticipant).filter_by(session_id=session.id).all():
        cid = str(getattr(p, "character_id", "") or "")
        if cid and cid not in ids:
            ids.append(cid)
    pc = str(session.player_character_id or "")
    if pc and pc not in ids:
        ids.insert(0, pc)
    return ids


def plan_session(
    db, session: GameSession, module: Module, scene_events: bool = False,
) -> tuple[dict, list[str]]:
    """重放一局的历史事件，返回（更新后的 world_state, 变更说明行）。"""
    ws = dict(session.world_state or {})
    notes: list[str] = []
    if module is None:
        return ws, notes

    scenes_by_id = {str(s.get("id") or ""): s for s in (module.scenes or [])}
    clue_names = {str(c.get("id") or ""): str(c.get("name") or "") for c in (module.clues or [])}
    who = _party_ids(db, session)

    events = (
        db.query(EventLog)
        .filter(EventLog.session_id == session.id)
        .order_by(EventLog.sequence_num)
        .all()
    )
    for ev in events:
        if ev.event_type not in ("narration", "dialogue"):
            continue
        text = (ev.content or "").strip()
        if not text:
            continue
        seq = int(ev.sequence_num or 0)
        scene_id = str((ev.metadata_ or {}).get("scene_id") or "")

        # 场景机制点：只对文该事件所在场景的 events（默认关，理由见模块 docstring）。
        for index, event in enumerate(
            ((scenes_by_id.get(scene_id) or {}).get("events") or []) if scene_events else []
        ):
            if not isinstance(event, dict):
                continue
            trigger = str(event.get("trigger") or "").strip()
            if not trigger or world_memory.scene_event_seen(ws, scene_id, index):
                continue
            if _scene_event_narrated(trigger, text):
                ws = world_memory.record_scene_event_seen(ws, scene_id, index, seq, note=trigger)
                notes.append(f"  机制点 seq={seq} {scene_id}[{index}] {trigger}")

        # 线索：只认绑定到该场景（或无绑定）的，别把别处的线索凭一个同名词记掉。
        ledger = dict(ws.get("clue_ledger") or {})
        for clue in module.clues or []:
            cid = str((clue or {}).get("id") or "").strip()
            loc = str((clue or {}).get("location") or "").strip()
            if not cid or cid in ledger or (loc and loc != scene_id):
                continue
            if _clue_shown_in_narration(clue, text):
                ws = world_memory.record_clue_reveal(
                    ws, [cid], "hint", who, seq,
                    note=f"叙事已提及{clue_names.get(cid) or cid}（历史回填）",
                )
                notes.append(f"  线索   seq={seq} {cid} {clue_names.get(cid)} @{scene_id}")
    return ws, notes


def main() -> int:
    ap = argparse.ArgumentParser(description="回填历史存档的线索台账与场景机制点进度")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="SQLite 路径")
    ap.add_argument("--session", default="", help="只处理这一局（默认全部）")
    ap.add_argument("--apply", action="store_true", help="实际写入（缺省只预演）")
    ap.add_argument(
        "--scene-events", action="store_true",
        help="连场景机制点一起回填（误标率偏高，见模块 docstring，慎用）",
    )
    args = ap.parse_args()

    engine = create_engine(f"sqlite:///{args.db}")
    db = sessionmaker(bind=engine)()

    q = db.query(GameSession)
    if args.session:
        q = q.filter(GameSession.id == args.session)
    sessions = q.all()

    total_clues = total_events = touched = 0
    for s in sessions:
        module = db.get(Module, s.module_id)
        before_ledger = len(dict((s.world_state or {}).get("clue_ledger") or {}))
        before_seen = len(dict((s.world_state or {}).get("scene_events_seen") or {}))
        ws, notes = plan_session(db, s, module, scene_events=args.scene_events)
        added_clues = len(dict(ws.get("clue_ledger") or {})) - before_ledger
        added_seen = len(dict(ws.get("scene_events_seen") or {})) - before_seen
        if not notes:
            continue
        touched += 1
        total_clues += added_clues
        total_events += added_seen
        print(
            f"[{s.id}] {getattr(module, 'title', '?')}："
            f"线索 +{added_clues}（原 {before_ledger}）｜机制点 +{added_seen}（原 {before_seen}）"
        )
        for line in notes:
            print(line)
        if args.apply:
            s.world_state = ws

    if args.apply:
        db.commit()
        print(f"\n已写入：{touched} 局，线索 +{total_clues}，机制点 +{total_events}")
    else:
        print(f"\n预演（未写入）：{touched} 局，线索 +{total_clues}，机制点 +{total_events}")
        print("确认无误后加 --apply 实际写入。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
