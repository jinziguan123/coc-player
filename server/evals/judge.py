"""裁判模型：对一段 KP 旁白按 rubric 逐项 0/1 打分。

与确定性检查互补：这里评的是语义层面（泄密暗示、计划遵循、替玩家行动、
出戏、衔接），机器规则查不出来的部分。fail-open：调用/解析失败返回 None，
运行器把该 fixture 标记为 judge_error 而不判失败。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.ai.turn_planner import TurnPlan

from evals.common import ReplayCase

logger = logging.getLogger(__name__)

RUBRIC = {
    "no_leak": "未泄露安全约束（do_not_reveal）中的隐藏信息，包括转述、总结、暗示",
    "plan_adherence": "遵循裁定计划：该发起的检定发起了（requires_check 时以 [DICE_CHECK] 收尾）、"
                      "线索按 reveal_level 揭示、未越过 clue_policy/npc_policy 的边界；无计划则只看是否自洽",
    "no_player_control": "未替任何玩家角色**新增**行动、说话、心理描写或决定。注意豁免："
                         "玩家本轮已明确宣言的动作，KP 对其做过程性展开（把「我敲击侧板找暗格」"
                         "渲染成俯身、叩击、摸索的画面）**不算违规**——那是检定前的正当铺陈；"
                         "违规是 KP 让玩家做了没宣言过的事、说了没说过的话、下了没下过的判断",
    "in_character": "始终是沉浸式叙事：无汇报体总结、无系统性/技术性语言、无跳出 KP 身份的元评论"
                    "（方括号指令是系统语法，不算违规）",
    "coherence": "与最近事件自然衔接：回应了玩家本轮的行动/发言，没有无视输入或凭空跳转",
    "subject_fidelity": "叙述主语归属正确：每个动作/所见/所得/所悟都落到正确的角色名下，没有张冠李戴。"
                        "尤其当最近事件里有某角色的检定结果时，该结果**只能**叙述成那名执行者的所得，"
                        "**绝不能安到别的角色头上**（如把甲的智力检定结果写成乙在推理/观察）。"
                        "只有一名玩家角色、或无检定归属歧义时，本项默认通过",
    "perception_isolation": "NPC 感知边界：每个 NPC 只对「本人在场目睹/听到、被当面告知、或隔墙可闻的巨响"
                            "（枪声/尖叫级）」的事作出反应。若上下文表明人员分处不同地点（不同场景，或叙事"
                            "确立的门/墙/楼层之隔），NPC 对别处发生的言行**不得**评论、追问、神色变化或表现出"
                            "任何知情。无 NPC 出场、或全员同处一地时，本项默认通过",
    "improvised_containment": "临场角色收容：模组未列出、KP 临时添加的龙套（见上下文「临场角色名单」）"
                              "**不得携带或产出线索、秘密、关键情报，不得把守剧情、成为唯一知情人**。"
                              "玩家追问时龙套如实不知、至多指回模组内容（模组 NPC/场景），不得现编往事或情报"
                              "来满足追问。无临场角色出场时本项默认通过",
    "combat_engine_authority": "战斗/追逐叙述只据引擎结算续写：**不自报具体伤害数字或骰点**、不臆造未发生的"
                               "命中/闪避/倒下、不替玩家决定攻击目标或防御方式；已倒下（昏迷/濒死/死亡/逃离）的"
                               "角色不再行动。非战斗轮次本项默认通过",
    "combat_turn_order": "结构化战斗轮里按先攻顺序推进、每人每轮一个主要动作，不让同一角色一轮内重复行动、"
                         "不越过轮到的行动者。非战斗轮次本项默认通过",
    "check_scope": "检定投的范围与它的驱动来源相符。判据是「谁引发了这次判定」而非技能名："
                   "① 由某角色的宣言驱动（他撬锁、他翻书架、他贴门细听）→ 只投他，指令写 char=<该角色> "
                   "或缺省；② 由世界一侧驱动、没有人宣言要接受它（环境变化、NPC 举动、机关触发、时间推进"
                   "带来的后果，如身后一声响、毒气弥漫、横梁砸落、不可名状之物显形，以及几乎所有「落在谁"
                   "头上」的幸运判定）→ **必须 char=在场**，让在场每人各投一次；只投主角等于凭空替其余人"
                   "免掉了风险或机会，判不通过。本轮没有 [DICE_CHECK]、或场上只有一名玩家角色时默认通过",
}

# 正向观测项：量化叙事质量走势（场景感/节奏），随 --repeat 看通过率与方差。
# **不参与 fixture 通过判定**——主观维度做门禁会让基线抖动；只记录、只对比趋势。
# 评判倾向与防守项相反：仅在缺陷明显时判不通过，拿不准就通过。
ADVISORY_RUBRIC = {
    "scene_texture": "【观测】场景感：旁白让当前场景可感——至少有一处具体的环境/感官细节"
                     "（视觉、声响、气味、光影、空间关系或物件质感），且与既有场景设定一致。"
                     "只在通篇是干瘪的事件陈述、或细节与既有描述矛盾时判不通过",
    "pacing": "【观测】节奏：篇幅与信息密度同本轮事件的分量相称——过场小事不铺陈成大段、"
              "关键揭示不一笔带过；没有原地打转的重复描写或凑字的空话；结尾收得干净"
              "（收在检定、提问或留白上），不拖泥带水。只在明显失衡时判不通过",
}

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)

#: 缺项即判 judge_error 的**核心项**：这几项任何轮次都适用，模型漏写只可能是输出坏了；
#: 而漏判它们的代价是实打实的（泄密、替玩家行动），宁可假阴性也不给假分。
_REQUIRED_KEYS = (
    "no_leak", "plan_adherence", "no_player_control", "in_character", "coherence",
)
#: 其余都是**条件项**——rubric 文本自己就写明「无 NPC 出场时默认通过」「非战斗轮次默认通过」
#: 这类前提。模型漏写一个它判定为不适用的项，本不该把另外十项的有效评分一起作废：
#: 代价与错误完全不成比例。缺失时按其既定的「默认通过」处理，只记 warning。
_CONDITIONAL_KEYS = tuple(k for k in RUBRIC if k not in _REQUIRED_KEYS)

#: 裁判内容层的重试次数。传输层在 Provider 里已重试 3 次，内容层（空响应/坏 JSON/缺核心项）
#: 此前却是一次判死——而裁判是低温结构化任务，重跑一次成本极低、成功率很高。这个不对称
#: 会让裁判的抖动系统性压低所有 fixture 的分数，且与被测内容无关。
JUDGE_MAX_ATTEMPTS = 3


def _recent_events_text(case: ReplayCase, limit: int = 12) -> str:
    lines = []
    for e in case.events[-limit:]:
        lines.append(f"[{e.event_type}] {e.actor_name or '系统'}: {(e.content or '')[:200]}")
    return "\n".join(lines) or "（无历史事件，本轮为开场）"


def build_judge_messages(
    case: ReplayCase, plan: TurnPlan | None, narration: str,
) -> list[dict]:
    plan_text = (
        json.dumps(plan.model_dump(), ensure_ascii=False) if plan else "（本轮无裁定计划）"
    )
    # 「投骰后续写」重放：KP 拿到的是这串检定结果（run_case 回灌 KP_DICE_CONTINUATION_PROMPT）。
    # 不喂给裁判，裁判就只能从历史事件里猜骰点——续写按大失败写、事件里却是旧的成功骰，
    # 会被误判成 plan_adherence/coherence 不过。
    dice_text = (
        "本轮投骰结果（KP 被要求据此续写；与历史事件里的骰点不一致时以本节为准）：\n"
        f"{case.continuation}\n\n"
        if case.continuation else ""
    )
    all_rubric = {**RUBRIC, **ADVISORY_RUBRIC}
    rubric_text = "\n".join(f"- {key}: {desc}" for key, desc in all_rubric.items())
    schema = ", ".join(f'"{k}": {{"pass": true, "reason": ""}}' for k in all_rubric)
    return [
        {
            "role": "system",
            "content": (
                "你是 TRPG 跑团质量评审，对 AI 守秘人（KP）生成的一段旁白逐项打分。"
                "严格按事实评判，不确定时倾向判不通过；"
                "但带【观测】标注的正向项倾向相反：仅在缺陷明显时判不通过，拿不准就通过。"
                "只输出一个 JSON object，不要输出 Markdown。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"玩家角色（KP 绝不可替他们行动/说话）：{', '.join(case.player_names)}\n\n"
                f"本轮裁定计划（KP 应遵循的约束）：\n{plan_text}\n\n"
                f"{dice_text}"
                f"最近事件（旁白应与之衔接）：\n{_recent_events_text(case)}\n\n"
                f"待评审的 KP 旁白：\n{narration}\n\n"
                f"评分项定义：\n{rubric_text}\n\n"
                f'对每项给出 pass（bool）与 reason（不通过时一句话说明，通过留空），'
                f"返回 {{{schema}}}。只输出 JSON。"
            ),
        },
    ]


def _parse_judge_output(raw: str) -> dict[str, dict] | None:
    text = raw.strip()
    fence = _JSON_FENCE_RE.search(text)
    if fence:
        text = fence.group(1)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    result = {}
    for key in _REQUIRED_KEYS:
        item = data.get(key)
        if not isinstance(item, dict) or "pass" not in item:
            return None  # 核心项缺失视为解析失败，宁可 judge_error 也不给假分
        result[key] = {"pass": bool(item["pass"]), "reason": str(item.get("reason") or "")}
    for key in _CONDITIONAL_KEYS:
        item = data.get(key)
        if not isinstance(item, dict) or "pass" not in item:
            # 条件项缺失 → 按 rubric 自带的「不适用时默认通过」处理，不废掉整份评分。
            logger.warning("judge 缺条件项 %s，按默认通过处理", key)
            result[key] = {"pass": True, "reason": "（裁判未返回该项，按默认通过处理）"}
            continue
        result[key] = {"pass": bool(item["pass"]), "reason": str(item.get("reason") or "")}
    for key in ADVISORY_RUBRIC:
        item = data.get(key)
        if not isinstance(item, dict) or "pass" not in item:
            continue  # 观测项缺失不构成 judge_error（不参与通过判定，宁缺毋假）
        result[key] = {"pass": bool(item["pass"]), "reason": str(item.get("reason") or "")}
    return result


def _retry_hint(last_raw: str) -> dict:
    """把上一次的坏输出回灌给裁判——只说「不合法」而不说错在哪，模型往往照原样再错一遍。"""
    detail = "返回了空响应" if not (last_raw or "").strip() else f"上次返回：{last_raw[:200]}"
    return {
        "role": "user",
        "content": (
            f"你上一次的输出不是合法的评分 JSON（{detail}）。"
            "请重新评审，**只输出 JSON 对象本身**：不要解释、不要前后文字、不要代码围栏，"
            "并确保包含全部评分项。"
        ),
    }


async def run_judge(
    llm: Any, case: ReplayCase, plan: TurnPlan | None, narration: str,
) -> dict[str, dict] | None:
    """返回 {rubric_key: {"pass": bool, "reason": str}}；连续失败 JUDGE_MAX_ATTEMPTS 次返回 None。

    三个失败通道（调用异常 / 空响应或坏 JSON / 缺核心项）统一重试，因为它们都是**裁判自己
    坏了**、与被测旁白无关；不重试的话这些抖动会被当成内容不合格记进通过率。
    第二次起把温度抬到 0.3：temperature=0 重跑几乎必然复现同一个坏输出，确定性在这里是负资产。
    """
    base = build_judge_messages(case, plan, narration)
    last_raw = ""
    for attempt in range(JUDGE_MAX_ATTEMPTS):
        messages = base if attempt == 0 else [*base, _retry_hint(last_raw)]
        try:
            raw = await llm.complete(
                messages,
                temperature=0.0 if attempt == 0 else 0.3,
                response_format={"type": "json_object"},
            )
        except Exception:
            logger.exception(
                "judge 调用失败（第 %d/%d 次）: fixture=%s",
                attempt + 1, JUDGE_MAX_ATTEMPTS, case.name,
            )
            last_raw = ""
            continue
        parsed = _parse_judge_output(raw)
        if parsed is not None:
            if attempt:
                logger.info("judge 第 %d 次重试成功: fixture=%s", attempt + 1, case.name)
            return parsed
        last_raw = raw or ""
        logger.warning(
            "judge 输出无法解析（第 %d/%d 次）: fixture=%s raw=%.200s",
            attempt + 1, JUDGE_MAX_ATTEMPTS, case.name, last_raw,
        )
    return None
