"""移动端到端验收：在隔离的存档副本上，经真实 HTTP 入口驱动整回合，录下模型输入输出与落库结果。

与 ``evals/run.py`` 的区别：那边只回放首段叙事与投骰续写；这里走玩家发言 → 推进 → 规划器 →
确定性移动裁定 → AI 队友 → KP 叙事 → 守卫 → 落库的完整编排，并在每个场景后取证：

- 玩家原始输入、规划器原始输出、发给 KP 的完整消息、KP 最终叙述（录制 Provider 每一次调用）；
- 数据库里的角色位置、移动事件、回合移动裁定与待检定条目、物品。

隔离：用 SQLite backup API 把 ``server/trpg.db`` 拷到临时目录（源库只读打开），所有会话建在副本里；
AI 配置只读取 ``server/ai_settings.json``，写入被拦截；场景配图与生图关闭。

用法（server 目录）：
    .venv/bin/python -m evals.e2e_movement --dry-run            # 假模型，验证接线，不花钱
    .venv/bin/python -m evals.e2e_movement --scenarios s1b,s4s  # 真实模型（激活配置）
结果写入 ``evals/results/e2e-<时间>/``（已被 gitignore）。
"""
from __future__ import annotations

import argparse
import asyncio
import contextvars
import json
import os
import sqlite3
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent
SOURCE_DB = SERVER / "trpg.db"
SOURCE_AI_SETTINGS = SERVER / "ai_settings.json"
TOMB_TITLE = "陵墓"
HOST_TOKEN = "e2e-host"

#: 固定骰值：阿尔文攀爬 20、常见力量 40+，15 为普通/困难成功；95 为失败（非大失败）。
ROLL_SUCCESS = 15
ROLL_FAILURE = 95


# ── 隔离环境：必须在导入 app 之前完成 ─────────────────────────────────

def _prepare_workdir(workdir: Path) -> Path:
    workdir.mkdir(parents=True, exist_ok=True)
    target = workdir / "trpg.db"
    src = sqlite3.connect(f"file:{SOURCE_DB}?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    os.environ["DB_PATH"] = str(target)
    os.environ["LOG_DIR"] = str(workdir / "logs")
    return target


# ── 调用录制 ─────────────────────────────────────────────────────────

_current_call: contextvars.ContextVar[dict | None] = contextvars.ContextVar("e2e_call", default=None)


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class Recorder:
    max_calls: int
    max_tokens: int
    calls: list[dict] = field(default_factory=list)
    scenario: str = ""

    def begin(self, method: str, messages, tools=None) -> dict:
        from app.ai import usage_tracker

        if len(self.calls) >= self.max_calls:
            raise BudgetExceeded(f"调用次数达到上限 {self.max_calls}")
        if self.total_tokens() >= self.max_tokens:
            raise BudgetExceeded(f"token 达到上限 {self.max_tokens}")
        record = {
            "scenario": self.scenario, "index": len(self.calls), "method": method,
            "phase": usage_tracker.current_phase(), "messages": [dict(m) for m in messages],
            "tools": [t.get("function", {}).get("name") if isinstance(t, dict) else str(t)
                      for t in (tools or [])],
            "text": "", "reasoning_chars": 0, "tool_calls": [], "usage": {}, "error": "",
            "started": time.time(),
        }
        self.calls.append(record)
        _current_call.set(record)
        return record

    def total_tokens(self) -> int:
        return sum(int((c.get("usage") or {}).get("total_tokens") or 0) for c in self.calls)


def _wrap(provider, recorder: Recorder):
    from app.ai.provider import LLMProvider

    class Recording(LLMProvider):
        def __getattr__(self, name):
            return getattr(provider, name)

        def supports_tools(self) -> bool:
            return provider.supports_tools()

        def supports_vision(self) -> bool:
            return provider.supports_vision()

        async def complete(self, messages, *args, **kwargs):
            rec = recorder.begin("complete", messages)
            try:
                out = await provider.complete(messages, *args, **kwargs)
                rec["text"] = out if isinstance(out, str) else repr(out)
                return out
            except Exception as error:
                rec["error"] = repr(error)
                raise

        async def complete_vision(self, *args, **kwargs):
            rec = recorder.begin("complete_vision", [])
            out = await provider.complete_vision(*args, **kwargs)
            rec["text"] = str(out)
            return out

        async def stream(self, messages, *args, **kwargs):
            rec = recorder.begin("stream", messages)
            try:
                async for piece in provider.stream(messages, *args, **kwargs):
                    rec["text"] += str(piece)
                    yield piece
            except Exception as error:
                rec["error"] = repr(error)
                raise

        async def stream_chat(self, messages, tools=None, *args, **kwargs):
            rec = recorder.begin("stream_chat", messages, tools)
            try:
                async for delta in provider.stream_chat(messages, tools, *args, **kwargs):
                    if delta.kind == "text":
                        rec["text"] += delta.text or ""
                    elif delta.kind == "reasoning":
                        rec["reasoning_chars"] += len(delta.text or "")
                    elif delta.kind == "tool_call" and delta.tool_call is not None:
                        rec["tool_calls"].append({
                            "name": delta.tool_call.name, "arguments": delta.tool_call.arguments,
                        })
                    yield delta
            except Exception as error:
                rec["error"] = repr(error)
                raise

    return Recording()


class FakeProvider:
    """--dry-run 用：不联网，只验证接线与取证流程。规划器拿到空计划，KP 写一句叙事。"""

    def supports_tools(self) -> bool:
        return True

    def supports_vision(self) -> bool:
        return False

    async def complete(self, messages, *args, **kwargs):
        return "{}"

    async def complete_vision(self, *args, **kwargs):
        return ""

    async def stream(self, messages, *args, **kwargs):
        yield "（假模型叙事）"

    async def stream_chat(self, messages, tools=None, *args, **kwargs):
        from app.ai.provider import StreamDelta

        yield StreamDelta(kind="text", text="（假模型叙事）")


def _install(recorder: Recorder, *, dry_run: bool) -> None:
    from app.ai import llm_factory, profile_store, usage_tracker
    from app.services import illustration_service

    profile_store.SETTINGS_FILE = SOURCE_AI_SETTINGS

    def _no_write(_data):
        raise RuntimeError("验收脚本禁止改写 AI 配置")

    profile_store._save_raw = _no_write
    profile_store.load_active_image_profile = lambda: None
    illustration_service._maybe_scene_illustration = lambda *a, **k: []

    original = llm_factory._provider_from_profile

    def provider_from_profile(profile):
        inner = FakeProvider() if dry_run else original(profile)
        return _wrap(inner, recorder)

    llm_factory._provider_from_profile = provider_from_profile

    original_add = usage_tracker.add

    def add(usage):
        rec = _current_call.get()
        if rec is not None and usage:
            for key, value in usage.items():
                if isinstance(value, (int, float)):
                    rec["usage"][key] = rec["usage"].get(key, 0) + value
        return original_add(usage)

    usage_tracker.add = add


def _pin_roll(value: int):
    from app.rules.coc.components import check

    check.roll_percentile = lambda: value


# ── 场景驱动 ─────────────────────────────────────────────────────────

class Table:
    """一张测试桌：克隆存档里的《陵墓》调查员，建会话、开局、落固定开场。"""

    def __init__(self, client, module, members: list[tuple[str, str]], *, start: str,
                 opening: str, kp_mode: str = "ai", positions: dict[str, str] | None = None):
        self.client = client
        self.module = module
        self.members = members           # [(源角色名前缀, "human"|"ai")]，第一个是房主
        self.start = start
        self.opening = opening
        self.kp_mode = kp_mode
        self.positions = positions or {}
        self.chars: dict[str, object] = {}
        self.tokens: dict[str, str] = {}
        self.sid = ""

    def setup(self, db) -> None:
        from app.models import Character, SessionParticipant
        from app.services import session_service

        source = {c.name: c for c in db.query(Character).filter(Character.name.isnot(None)).all()}
        seats = []
        for index, (prefix, role) in enumerate(self.members):
            src = next(c for name, c in source.items() if name.startswith(prefix))
            clone = Character(
                name=src.name.split("·")[0] if index else src.name, rule_system=src.rule_system,
                is_player=role == "human", base_attributes=dict(src.base_attributes or {}),
                skills=dict(src.skills or {}), system_data=dict(src.system_data or {}),
                backstory=src.backstory or "",
            )
            db.add(clone)
            db.flush()
            self.chars[prefix] = clone
            seats.append({"character_id": clone.id, "role": role, "is_primary": index == 0})
        db.commit()
        session = session_service.create_session(
            db, self.module.id, seats, creator_token=HOST_TOKEN, kp_mode=self.kp_mode,
        )
        self.sid = session.id
        for index, (prefix, role) in enumerate(self.members):
            if role != "human":
                continue
            token = HOST_TOKEN if index == 0 else f"e2e-{index}"
            self.tokens[prefix] = token
            seat = db.query(SessionParticipant).filter_by(
                session_id=self.sid, character_id=self.chars[prefix].id,
            ).one()
            seat.owner_token, seat.claimed, seat.ready = token, True, True
        db.commit()
        session_service.start_game(db, self.sid, HOST_TOKEN)
        db.refresh(session)
        session.current_scene_id = self.start
        db.commit()
        for prefix, scene in self.positions.items():
            session_service.set_char_location(db, self.sid, self.chars[prefix].id, scene)
        session_service.add_event(db, self.sid, "narration", self.opening, actor_name="KP")

    async def wait(self) -> None:
        from app.services.generation_manager import generation_manager

        await asyncio.sleep(0.05)
        while generation_manager.is_generating(self.sid):
            await asyncio.sleep(0.2)

    async def turn(self, lines: list[tuple[str, str]]) -> None:
        """lines: [(角色前缀, 原话)]；全部入暂存后由房主推进。"""
        for prefix, text in lines:
            r = await self.client.post(
                f"/api/sessions/{self.sid}/chat",
                json={"content": text, "acting_character_id": self.chars[prefix].id},
                headers={"X-Player-Token": self.tokens[prefix]},
            )
            r.raise_for_status()
        host = self.members[0][0]
        r = await self.client.post(
            f"/api/sessions/{self.sid}/advance",
            json={"acting_character_id": self.chars[host].id},
            headers={"X-Player-Token": self.tokens[host]},
        )
        r.raise_for_status()
        await self.wait()

    async def roll(self, db, prefix: str, value: int) -> bool:
        from app.models import GameSession

        db.expire_all()
        session = db.get(GameSession, self.sid)
        pending = (session.turn_state or {}).get("pending_checks") or {}
        char_id = self.chars[prefix].id
        check_id = next((cid for cid, c in pending.items() if c.get("char_id") == char_id), None)
        if check_id is None:
            return False
        _pin_roll(value)
        r = await self.client.post(
            f"/api/sessions/{self.sid}/roll", json={"check_id": check_id},
            headers={"X-Player-Token": self.tokens[prefix]},
        )
        r.raise_for_status()
        await self.wait()
        # 村规「燃运」会在失败后停下问要不要花幸运；验收按「放弃」走完结算链（与玩家点放弃同一入口）。
        db.expire_all()
        if (db.get(GameSession, self.sid).turn_state or {}).get("pending_luck"):
            r = await self.client.post(
                f"/api/sessions/{self.sid}/luck-decision", json={"spend": False, "reroll": False},
                headers={"X-Player-Token": self.tokens[prefix]},
            )
            r.raise_for_status()
            await self.wait()
        return True

    async def regenerate(self) -> None:
        r = await self.client.post(
            f"/api/sessions/{self.sid}/regenerate", headers={"X-Player-Token": HOST_TOKEN},
        )
        r.raise_for_status()
        await self.wait()

    async def kp_action(self, action: str, payload: dict) -> dict:
        r = await self.client.post(
            f"/api/sessions/{self.sid}/kp/action", json={"action": action, "payload": payload},
            headers={"X-Player-Token": HOST_TOKEN},
        )
        r.raise_for_status()
        return r.json()

    def snapshot(self, db) -> dict:
        from app.models import Character, GameSession
        from app.services import movement, session_service

        db.expire_all()
        session = db.get(GameSession, self.sid)
        events = session_service.get_session_events(db, self.sid)
        ts = session.turn_state or {}
        return {
            "locations": {p: session_service.get_char_location(session, c.id)
                          for p, c in self.chars.items()},
            "events": [{"seq": e.sequence_num, "type": e.event_type, "actor": e.actor_name,
                        "content": e.content, "meta": e.metadata_ or {}} for e in events],
            "move_events": [e.metadata_ for e in events if (e.metadata_ or {}).get("kind") == "move"],
            "turn_moves": list(ts.get(movement.MOVES_KEY) or []),
            "pending_moves": list(ts.get(movement.PENDING_KEY) or []),
            "pending_checks": ts.get("pending_checks") or {},
            "inventory": {p: (db.get(Character, c.id).system_data or {}).get("inventory")
                          for p, c in self.chars.items()},
        }


# ── 场景定义 ─────────────────────────────────────────────────────────

ALVIN, NADIA, KARIM, KLAUS = "阿尔文", "纳迪娅", "卡里姆", "克劳斯"
OPEN_CORRIDOR = (
    "你们站在长约二十五英尺的甬道里，墙上是磨损的象形文字与壁画。甬道尽头是一扇被封死的拱门，"
    "左下角有一个被凿开的石膏墙洞，刚好容一人钻过，洞后一片漆黑。"
)
OPEN_HIGH_HOLE = (
    "你们站在甬道里。尽头的拱门被封死，左上方离地一人多高处有一个被凿开的石膏墙洞，"
    "边缘松脆、不好着力，得先攀爬上去才能钻进洞里。"
)
OPEN_ANTECHAMBER = (
    "你们站在前室里，四周堆满宝物，西北角有金制战车和划艇。东侧拱门被一块巨大的石板挡住，"
    "两侧立着兽首木乃伊守卫；北侧拱门通往一间耳室。"
)
OPEN_CHAPEL = (
    "你们站在耳室里，墙上有丧葬锥和两幅大型壁画。东墙神龛前倒着一具骷髅，旁边有一个旧挎包。"
)


async def s1a(table_factory):
    t = table_factory([(ALVIN, "human"), (NADIA, "ai"), (KARIM, "ai"), (KLAUS, "ai")],
                      start="scene_1", opening=OPEN_CORRIDOR)
    yield t
    await t.turn([(ALVIN, "我抛下队友，从拱门钻进去")])
    yield "turn1"


async def s1b(table_factory):
    t = table_factory([(ALVIN, "human"), (NADIA, "ai"), (KARIM, "ai"), (KLAUS, "ai")],
                      start="scene_1", opening=OPEN_CORRIDOR)
    yield t
    await t.turn([(ALVIN, "我抛下队友，从墙洞钻进去")])
    yield "turn1"


async def s2(table_factory):
    t = table_factory([(ALVIN, "human"), (NADIA, "human"), (KARIM, "ai")],
                      start="scene_1", opening=OPEN_CORRIDOR)
    yield t
    await t.turn([(NADIA, "我留在甬道守着出口，你们先进去。"), (ALVIN, "我们走吧，从墙洞钻进去。")])
    yield "turn1"


async def s3(table_factory):
    t = table_factory([(ALVIN, "human"), (NADIA, "human")], start="scene_1", opening=OPEN_CORRIDOR)
    yield t
    await t.turn([(ALVIN, "我们要不要从那个墙洞钻进去？里面会不会有危险？")])
    yield "discuss"
    await t.turn([(ALVIN, "我走到墙洞前，举灯往里照一照，看看有什么。")])
    yield "approach"
    await t.turn([(ALVIN, "如果里面没有动静，我就钻进去。")])
    yield "conditional"


def _gate_table(table_factory):
    return table_factory([(ALVIN, "human"), (NADIA, "human")], start="scene_2", opening=OPEN_ANTECHAMBER)


async def s4s(table_factory, db):
    t = _gate_table(table_factory)
    yield t
    await t.turn([(ALVIN, "我用肩膀顶住东侧拱门前的石板，使劲把它推开，然后钻进去。")])
    yield "before_roll"
    rolled = await t.roll(db, ALVIN, ROLL_SUCCESS)
    yield f"after_roll_success(rolled={rolled})"


async def s4f(table_factory, db):
    t = _gate_table(table_factory)
    yield t
    await t.turn([(ALVIN, "我用肩膀顶住东侧拱门前的石板，使劲把它推开，然后钻进去。")])
    yield "before_roll"
    rolled = await t.roll(db, ALVIN, ROLL_FAILURE)
    yield f"after_roll_failure(rolled={rolled})"


async def s5(table_factory, db):
    t = table_factory([(ALVIN, "human"), (KLAUS, "human")], start="scene_1", opening=OPEN_HIGH_HOLE)
    yield t
    await t.turn([(ALVIN, "我先爬上去，从墙洞钻进去。"), (KLAUS, "我也爬上去，从墙洞钻进去。")])
    yield "before_roll"
    a = await t.roll(db, ALVIN, ROLL_SUCCESS)
    yield f"after_alvin_success(rolled={a})"
    k = await t.roll(db, KLAUS, ROLL_FAILURE)
    yield f"after_klaus_failure(rolled={k})"


async def s6(table_factory):
    t = table_factory([(ALVIN, "human"), (NADIA, "human")], start="scene_1", opening=OPEN_CORRIDOR)
    yield t
    await t.turn([(ALVIN, "我抛下队友，独自从墙洞钻进去。")])
    yield "split"
    await t.turn([(ALVIN, "我从墙洞钻回甬道，回到纳迪娅身边。")])
    yield "regroup"
    await t.turn([(ALVIN, "我们一起看看墙上的壁画。"), (NADIA, "我举灯照着壁画，看看画的是什么。")])
    yield "together"


async def s7a(table_factory):
    t = table_factory([(ALVIN, "human"), (NADIA, "human")], start="scene_1", opening=OPEN_CORRIDOR)
    yield t
    await t.turn([(ALVIN, "我抛下队友，独自从墙洞钻进去。")])
    yield "turn1"
    await t.regenerate()
    yield "regenerated"


async def s7b(table_factory):
    t = table_factory([(ALVIN, "human")], start="scene_3", opening=OPEN_CHAPEL)
    yield t
    await t.turn([(ALVIN, "我捡起骷髅旁边的旧挎包，背到自己身上。")])
    yield "turn1"
    await t.regenerate()
    yield "regenerated"


async def s8(table_factory):
    t = table_factory([(ALVIN, "human"), (NADIA, "ai"), (KARIM, "ai")], start="scene_1",
                      opening=OPEN_CORRIDOR, kp_mode="human")
    yield t
    result = await t.kp_action("move_actor", {"character_id": t.chars[ALVIN].id, "scene_id": "scene_2"})
    yield f"kp_moved_host({result.get('result')})"


SCENARIOS = {
    "s1a": s1a, "s1b": s1b, "s2": s2, "s3": s3, "s4s": s4s, "s4f": s4f,
    "s5": s5, "s6": s6, "s7a": s7a, "s7b": s7b, "s8": s8,
}


# ── 主流程 ───────────────────────────────────────────────────────────

async def _run(args, recorder: Recorder, outdir: Path) -> None:
    import inspect

    import httpx

    from app import database
    from app.main import app
    from app.models import Module

    db = database.SessionLocal()
    module = db.query(Module).filter(Module.title == TOMB_TITLE).one()
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://e2e") as client:
        names = [n.strip() for n in args.scenarios.split(",") if n.strip()]
        for repeat in range(args.repeat):
            for name in names:
                label = f"{name}#{repeat + 1}"
                recorder.scenario = label
                fn = SCENARIOS[name]
                tables = []

                def factory(members, **kw):
                    table = Table(client, module, members, **kw)
                    table.setup(db)
                    tables.append(table)
                    return table

                params = inspect.signature(fn).parameters
                gen = fn(factory, db) if "db" in params else fn(factory)
                steps = []
                started = len(recorder.calls)
                error = ""
                try:
                    table = await gen.__anext__()
                    steps.append({"step": "initial", **table.snapshot(db)})
                    async for step in gen:
                        steps.append({"step": step, **table.snapshot(db)})
                        print(f"[{label}] {step}: {steps[-1]['locations']}", flush=True)
                except BudgetExceeded as exc:
                    error = f"预算上限：{exc}"
                except Exception as exc:  # 取证优先：记录后继续下一个场景
                    import traceback
                    error = traceback.format_exc()
                    print(f"[{label}] 出错：{exc!r}", flush=True)
                calls = recorder.calls[started:]
                result = {
                    "scenario": label, "session_id": tables[0].sid if tables else "",
                    "members": tables[0].members if tables else [], "steps": steps, "error": error,
                    "calls": calls,
                    "usage": {k: sum(int((c.get("usage") or {}).get(k) or 0) for c in calls)
                              for k in ("prompt_tokens", "completion_tokens", "reasoning_tokens",
                                        "cache_read_tokens", "total_tokens")},
                }
                (outdir / f"{label}.json").write_text(
                    json.dumps(result, ensure_ascii=False, indent=1, default=str), "utf-8",
                )
                print(f"[{label}] 调用 {len(calls)} 次，usage={result['usage']}", flush=True)
                if error.startswith("预算上限"):
                    print("已达预算上限，停止。", flush=True)
                    return
    db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenarios", default=",".join(SCENARIOS))
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-calls", type=int, default=400)
    parser.add_argument("--max-tokens", type=int, default=3_000_000)
    parser.add_argument("--workdir", default="")
    args = parser.parse_args()

    workdir = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix="coc-e2e-"))
    db_copy = _prepare_workdir(workdir)
    sys.path.insert(0, str(SERVER))
    from app.config import settings

    assert Path(settings.db_path) == db_copy, "未指向隔离副本，停止以免写入正式存档"
    recorder = Recorder(args.max_calls, args.max_tokens)
    _install(recorder, dry_run=args.dry_run)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    outdir = SERVER / "evals" / "results" / f"e2e-{stamp}{'-dry' if args.dry_run else ''}"
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"隔离存档：{db_copy}\n结果目录：{outdir}", flush=True)
    asyncio.run(_run(args, recorder, outdir))
    total = {k: sum(int((c.get("usage") or {}).get(k) or 0) for c in recorder.calls)
             for k in ("prompt_tokens", "completion_tokens", "reasoning_tokens", "cache_read_tokens",
                       "total_tokens")}
    (outdir / "summary.json").write_text(json.dumps(
        {"calls": len(recorder.calls), "usage": total, "dry_run": args.dry_run,
         "scenarios": args.scenarios, "repeat": args.repeat}, ensure_ascii=False, indent=1), "utf-8")
    print(f"总调用 {len(recorder.calls)} 次，usage={total}", flush=True)


if __name__ == "__main__":
    main()
