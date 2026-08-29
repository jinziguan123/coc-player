"""导演信号：从事件流 + 模组 + world_state 确定性地算出「节奏经营」线索。

这些信号是给 KP 回合规划器的**输入提示**（不是硬规则）：规划器据此产出
``DirectionPolicy``（怎么讲、给谁戏份、如何解卡），只影响叙事表达，绝不改动世界状态。
因此信号允许是近似的启发式——宁可给规划器一个软提示，也不追求精确。

纯函数、无副作用、不触数据库；异常由调用方兜底（导演是增强件，失败即退化为无提示）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services import world_memory

# 聚光灯：在最近这么多条事件里统计每个玩家角色的「出场」次数
SPOTLIGHT_WINDOW = 30
# 某角色 0 次出场、而他人出场达到此阈值 → 判定其被冷落
SPOTLIGHT_STARVED_THRESHOLD = 3
# 距上次「有进展」超过这么多个玩家回合 → 判定卡关
STUCK_THRESHOLD = 3
# 未解悬念最多罗列几条（避免灌爆规划器输入）
MAX_THREADS = 4
# 节奏单调：最近这么多个玩家回合若清一色是调查（无 NPC 对话）→ 提示换气
PACING_WINDOW = 5
# 临场龙套被提及这么多次仍未转正 → 建议收编（他显然已经在桌上站住了）
PROMOTE_MENTION_THRESHOLD = 4
# 建议收编最多列几个，避免灌爆规划器输入
MAX_PROMOTABLE = 3
# 判定「剧情已抵达结局」时，线索至少要被完整掌握到这个比例
ENDING_CLUE_RATIO = 0.8
# 模组至少要有这么多个「节拍」（触发器 + 线索）才谈得上「走完了」。
# 否则一个只有一条线索的模组，线索一被发现就会被判成结局——那不是结局，是刚开始。
ENDING_MIN_BEATS = 3


@dataclass
class DirectorSignals:
    spotlight_starved: list[str] = field(default_factory=list)  # 被冷落的玩家角色名
    stuck: bool = False
    stuck_turns: int = 0
    unresolved_threads: list[str] = field(default_factory=list)  # 悬念描述（一句话）
    monotonous: bool = False  # 近期节奏单调（长时间纯调查）
    promotable_npcs: list[str] = field(default_factory=list)  # 反复出场、值得收编的临场龙套
    ending_ready: bool = False  # 主线看起来已经走完，可以提示玩家收束

    def has_actionable(self) -> bool:
        """是否有值得注入规划器的信号——未解悬念单独存在不算（那是常态）。"""
        return bool(
            self.spotlight_starved or self.stuck or self.monotonous
            or self.promotable_npcs or self.ending_ready
        )

    def to_prompt(self) -> str:
        """渲染成给规划器看的一段中文提示（只在 has_actionable 或有悬念时调用）。"""
        lines: list[str] = []
        if self.spotlight_starved:
            lines.append(
                f"- 冷场角色：{'、'.join(self.spotlight_starved)} 最近几乎没有戏份，"
                "本轮应主动给他们登场机会（spotlight 列出他们）。"
            )
        if self.stuck:
            lines.append(
                f"- 卡关迹象：已连续约 {self.stuck_turns} 个玩家回合没有实质进展"
                "（无新线索、无场景推进）。考虑用 nudge 给一个不越俎代庖的推动"
                "（让某条线索更显眼、或让 NPC 主动接触），pacing 可设为 tighten。"
            )
        if self.monotonous:
            lines.append(
                "- 节奏单调：最近清一色是调查动作、缺少人物互动或情绪起伏。"
                "考虑 release 一下节奏，安排一段对话/氛围/意外来换气。"
            )
        if self.promotable_npcs:
            lines.append(
                f"- 已站住脚的临场角色：{'、'.join(self.promotable_npcs)} 已多次出场却还没有正式设定。"
                "继续使用他们时请保持前后一致的身份与说话方式（房主可在「临场角色」里把他们收编为正式配角）。"
            )
        if self.ending_ready:
            lines.append(
                "- 主线看起来已经走完（该触发的都触发了、线索也基本掌握）。"
                "本轮适合收束：把已知真相串起来给一个有分量的收尾，"
                "并**提示玩家可以结束本模组**——但结束与否由玩家自己决定，你不要代为宣布结束。"
            )
        if self.unresolved_threads:
            lines.append(
                "- 待回收悬念（择机推进或回收，勿全部抛出）：\n    "
                + "\n    ".join(self.unresolved_threads)
            )
        return "\n".join(lines)


def _event_type(ev: Any) -> str:
    return getattr(ev, "event_type", None) or getattr(ev, "type", "") or ""


def _actor_name(ev: Any) -> str:
    return (getattr(ev, "actor_name", None) or getattr(ev, "speaker", "") or "").strip()


def _content(ev: Any) -> str:
    return getattr(ev, "content", "") or ""


def _seq(ev: Any) -> int:
    return int(getattr(ev, "sequence_num", 0) or 0)


def _is_player_turn(ev: Any, player_names: set[str]) -> bool:
    """一条「玩家回合」事件：玩家角色主动发出的行动或台词。"""
    return _event_type(ev) in ("action", "dialogue") and _actor_name(ev) in player_names


def compute_spotlight_starved(events: list[Any], player_names: list[str]) -> list[str]:
    """最近窗口内 0 次出场、而队伍里有人出场 ≥ 阈值 → 被冷落。

    「出场」= 作为 actor 行动/发言，或在旁白里被点名（名字出现在 narration 文本中）。
    单人（无队友）时不判定——没有对比意义。
    """
    if len(player_names) < 2:
        return []
    counts = {name: 0 for name in player_names}
    for ev in events[-SPOTLIGHT_WINDOW:]:
        actor = _actor_name(ev)
        if actor in counts and _event_type(ev) in ("action", "dialogue"):
            counts[actor] += 1
        elif _event_type(ev) == "narration":
            text = _content(ev)
            for name in player_names:
                if name and name in text:
                    counts[name] += 1
    if not counts:
        return []
    top = max(counts.values())
    if top < SPOTLIGHT_STARVED_THRESHOLD:
        return []
    return [name for name, c in counts.items() if c == 0]


def _last_progress_seq(events: list[Any], world_state: dict) -> int:
    """最近一次「有进展」的事件序号：新线索发现（台账 seq）或场景切换。"""
    progress = 0
    for entry in (world_state.get("clue_ledger") or {}).values():
        progress = max(progress, int((entry or {}).get("seq") or 0))
    # 场景切换：相邻旁白的 scene_id 发生变化，视为一次推进
    prev_scene = None
    for ev in events:
        if _event_type(ev) != "narration":
            continue
        scene = (getattr(ev, "metadata_", None) or {}).get("scene_id")
        if scene and prev_scene is not None and scene != prev_scene:
            progress = max(progress, _seq(ev))
        if scene:
            prev_scene = scene
    return progress


def compute_stuck(events: list[Any], world_state: dict, player_names: list[str]) -> tuple[bool, int]:
    """距上次进展的玩家回合数 ≥ 阈值 → 卡关。返回 (是否卡关, 回合数)。"""
    names = set(player_names)
    since = _last_progress_seq(events, world_state)
    turns = sum(
        1 for ev in events if _is_player_turn(ev, names) and _seq(ev) > since
    )
    return turns >= STUCK_THRESHOLD, turns


def compute_unresolved_threads(module: Any, world_state: dict) -> list[str]:
    """未触发的 triggers + 台账中 partial 的线索，罗列供规划器择机回收。"""
    threads: list[str] = []
    active_flags = set(world_state.get("flags") or [])
    for trig in (getattr(module, "triggers", None) or []):
        set_flags = trig.get("set_flags") or []
        if set_flags and all(f in active_flags for f in set_flags):
            continue  # 已触发
        desc = (trig.get("description") or trig.get("when") or "").strip()
        if desc:
            threads.append(f"（未触发）{desc}")
    ledger = world_state.get("clue_ledger") or {}
    clue_names = {c.get("id"): c.get("name", "") for c in (getattr(module, "clues", None) or [])}
    for cid, entry in ledger.items():
        if (entry or {}).get("status") == "partial":
            name = clue_names.get(cid) or cid
            threads.append(f"（线索仅部分掌握）{name}")
    return threads[:MAX_THREADS]


def compute_monotonous(events: list[Any], player_names: list[str]) -> bool:
    """最近若干玩家回合清一色是调查（有掷骰/action、无 NPC 对话）→ 节奏单调。"""
    names = set(player_names)
    player_turns = [ev for ev in events if _is_player_turn(ev, names)]
    if len(player_turns) < PACING_WINDOW:
        return False
    recent = events[-(PACING_WINDOW * 3):]  # 覆盖玩家回合与其间的旁白/掷骰
    has_dialogue = any(
        _event_type(ev) == "dialogue" and _actor_name(ev) not in names
        for ev in recent
    )
    dice_or_action = sum(
        1 for ev in recent if _event_type(ev) in ("dice", "action")
    )
    return (not has_dialogue) and dice_or_action >= PACING_WINDOW


def compute_promotable_npcs(world_state: dict) -> list[str]:
    """反复出场却仍未收编的临场龙套。

    AI 会即兴造出模组里没有的角色；他们只活在事件流里，上下文一截断人设就散了。
    ``mentions`` 是登记侧已有的计数——出场够多次就说明这个龙套在桌上站住了，
    值得提醒把他固化成有卡的配角。
    """
    improv = (world_state or {}).get("improvised_npcs") or {}
    out: list[str] = []
    for name, entry in improv.items():
        entry = entry if isinstance(entry, dict) else {}
        if (entry.get("card") or {}).get("id"):
            continue  # 已转正
        if int(entry.get("mentions", 0)) < PROMOTE_MENTION_THRESHOLD:
            continue
        if not world_memory.is_plausible_npc_name(name):
            continue  # 台词归属误命中留下的垃圾名
        out.append(name)
    # 出场多的排前面，截断避免灌爆输入
    out.sort(key=lambda n: -int((improv.get(n) or {}).get("mentions", 0)))
    return out[:MAX_PROMOTABLE]


def compute_ending_ready(module: Any, world_state: dict) -> bool:
    """主线是否已经走完——用于**提示**玩家可以结束，绝不代为结束。

    判据全部取自既有数据，不需要模组额外声明：
      ① 模组的 triggers 全部已触发（set_flags 都进了 flags）；
      ② 线索台账里 status=known 的比例达到 ENDING_CLUE_RATIO。
    前置门槛：模组的触发器 + 线索合计不少于 ENDING_MIN_BEATS，内容太少不做判断——
    只有一条线索的模组，线索一被发现就喊结局，那不是结局是刚开始。
    """
    world_state = world_state or {}
    active_flags = set(world_state.get("flags") or [])

    triggers = [t for t in (getattr(module, "triggers", None) or []) if (t.get("set_flags") or [])]
    clues_all = getattr(module, "clues", None) or []
    # 内容太少的模组不做判断：一条线索被发现不等于故事结束。
    if len(triggers) + len(clues_all) < ENDING_MIN_BEATS:
        return False

    if triggers:
        if not all(
            all(f in active_flags for f in (t.get("set_flags") or []))
            for t in triggers
        ):
            return False

    clues = getattr(module, "clues", None) or []
    if clues:
        ledger = world_state.get("clue_ledger") or {}
        known = sum(
            1 for c in clues
            if ((ledger.get(c.get("id")) or {}).get("status")) == "known"
        )
        if known < len(clues) * ENDING_CLUE_RATIO:
            return False

    # 两样都没有 → 无从判断，不吭声
    return bool(triggers or clues)


def compute_signals(
    events: list[Any], module: Any, world_state: dict, player_names: list[str],
) -> DirectorSignals:
    world_state = world_state or {}
    stuck, stuck_turns = compute_stuck(events, world_state, player_names)
    return DirectorSignals(
        spotlight_starved=compute_spotlight_starved(events, player_names),
        stuck=stuck,
        stuck_turns=stuck_turns,
        unresolved_threads=compute_unresolved_threads(module, world_state),
        monotonous=compute_monotonous(events, player_names),
        promotable_npcs=compute_promotable_npcs(world_state),
        ending_ready=compute_ending_ready(module, world_state),
    )
