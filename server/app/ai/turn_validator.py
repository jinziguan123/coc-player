from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Iterable
from typing import Any

from pydantic import BaseModel, ValidationError

from app.ai.text_guard import has_near_duplicate_passages
from app.ai.turn_planner import TurnPlan, _extract_json_object

logger = logging.getLogger(__name__)


class TurnValidation(BaseModel):
    violated: bool = False
    reason: str = ""
    corrected_narration: str = ""


# 汇报体特征：全角标题（≤12 字）紧跟一段项目符号列表——KP 应该写故事，不该写这种总结报告。
_REPORT_STYLE_RE = re.compile(r"【[^】\n]{1,12}】\s*\n(?:\s*[-•]\s*.+\n?){1,}")
# 内部标识特征：flag_xxx / flag xxx 这类技术性 token，正常叙事文本不会写出来。
_INTERNAL_ID_RE = re.compile(r"\bflag[_ ][a-z0-9_]+", re.IGNORECASE)

# 否定式对比句式（"不是X，是Y" / "不是X而是Y" / "与其说…不如说…" / "这不是…，这是…"）：
# 各家 LLM 头号「显得文学」的口头禅，密集复用则空洞、审美疲劳。这是唯一真源——
# evals 的文风探针与 KP 上下文的反 tic 反馈环 nudge 都从这里取，避免规则各写一份漂移。
# 逗号前谓语段刻意排除逗号，避免「不是本地人，房子是租的」这类跨主语并列句误命中。
_ANTITHESIS_RE = re.compile(
    r"不是[^。！？；，\n]{1,30}?(?:而是|，(?:而是|却是|倒是|反倒是|反而是|才是|是))"
    r"|并非[^。！？；，\n]{1,30}?而是"
    r"|与其说[^。！？；\n]{1,30}?不如说"
    r"|这不是[^。！？；\n]{1,30}?，[^。！？；\n]{0,4}?这(?:是|才是)"
)


def count_antithesis(text: str) -> int:
    """统计一段文本里否定式对比句式的出现次数（文风单一化的量化指标）。"""
    return len(_ANTITHESIS_RE.findall(text or ""))


#: 破折号密度阈值（每千字个数）。
#:
#: 拿本机 400 段真实旁白量过：中位 6.3、75 分位 8.0、90 分位 10.3、最高 12.6——
#: 每千字 6 个就是每一百六十字一个，而且几乎全是「追加一句解释」的用法
#: （「他张了张嘴——作为乘务员，他认得出那块面板」）。这是写顺手带出来的口头禅。
#:
#: 定在 75 分位而不是中位：破折号在中文里是合法修辞（语气中断、插入语），
#: 卡到中位会有一半轮次触发，等于常驻指令，也就没有「按密度动态施压」可言了。
EM_DASH_PER_KILO = 8.0

#: 短文本算不出密度：两百字里出现一个破折号是 5.0/千字，看着超标，其实很正常。
_DENSITY_MIN_CHARS = 400


def em_dash_density(text: str) -> float:
    """每千字破折号个数；文本太短时返回 0（样本不足，不作数）。"""
    body = text or ""
    if len(body) < _DENSITY_MIN_CHARS:
        return 0.0
    return body.count("——") / (len(body) / 1000)


def _looks_suspicious(
    narration: str, plan: TurnPlan, turn_inputs: str = "", location_context: str = "",
) -> bool:
    """零成本预筛：值不值得为这段旁白多花一次 LLM 校验调用。

    有硬性隐藏信息（safety.do_not_reveal）时，语义泄露的代价高，值得付这次调用成本；
    有本轮玩家输入时还需检查输入回显和越权代演；回复内部出现明显近重复时同样校验。
    带位置硬约束时必然校验——旁白已经出现「人在 B、地图在 A」的分裂，必须改写落库版本。
    """
    if plan.safety.do_not_reveal:
        return True
    if location_context.strip():
        return True
    if _REPORT_STYLE_RE.search(narration):
        return True
    if _INTERNAL_ID_RE.search(narration):
        return True
    if turn_inputs.strip():
        return True
    if has_near_duplicate_passages(narration):
        return True
    return False


def build_validator_messages(
    plan: TurnPlan, narration: str, seen_context: str = "",
    turn_inputs: str = "", party_names: Iterable[str] | None = None,
    location_context: str = "", settled_checks: str = "",
) -> list[dict]:
    do_not_reveal = json.dumps(plan.safety.do_not_reveal, ensure_ascii=False)
    seen_block = (
        "\n\n【玩家已可感知/近期已看到的内容】（这些已经在明面上，再次描写不算泄露）：\n"
        + seen_context.strip() + "\n"
    ) if seen_context.strip() else ""
    turn_block = (
        "\n\n【本轮已在界面展示的玩家/队友消息】（只能承接，不得复述、润色或代演）：\n"
        + turn_inputs.strip() + "\n"
    ) if turn_inputs.strip() else ""
    # 没有这一段的话，终检只看得见玩家说了什么、看不见骰子——于是「宣言查登记簿 →
    # 图书馆使用通过 → 写他翻到了哪几条」会被判成代演。那正是检定通过该有的结果。
    checks_block = (
        "\n\n【本轮已经掷过的检定】（结果已定，写出它的成败、以及角色为此做了什么、"
        "因此看到或得到了什么，都是 KP 的本职）：\n"
        + settled_checks.strip() + "\n"
    ) if settled_checks.strip() else ""
    location_block = (
        "\n\n【系统确定的当前位置硬约束】（旁白发生地点必须与此一致，违反必须改写）：\n"
        + location_context.strip() + "\n"
    ) if location_context.strip() else ""
    # 名单是「代演」这条的判据。不给的话校验器只能靠猜——线上真实误判过：一个一路
    # 跟着主角行动、台词很多的 NPC（香澄澪）被当成了队友，于是它把这个 NPC 的动作、
    # 姿势、内心统统当作「代演玩家」删掉，等于把 KP 该写的东西改没了。
    roster = [n.strip() for n in (party_names or []) if str(n).strip()]
    party_block = (
        "\n\n【玩家一侧的角色名单】（只有这些名字算「玩家/队友」）：\n"
        + "、".join(roster)
        + "\n**名单之外的一切角色都是 NPC**，由 KP 扮演——写他们的动作、姿势、表情、"
          "内心与台词是 KP 的本职，绝不算代演。判断第 3、4 条时只认这份名单，不要按"
          "「谁戏份多」「谁跟主角同行」去猜。\n"
    ) if roster else ""
    return [
        {
            "role": "system",
            "content": (
                "你是 TRPG 回合边界校验器，检查旁白是否泄露秘密、复述已展示输入、"
                "越权操控玩家角色或在同一回复中重复起笔。"
                "只输出一个 JSON object，不要输出 Markdown。"
            ),
        },
        {
            "role": "user",
            "content": (
                "本轮必须对玩家保密的**隐藏真相**（其身份/本质/成因/幕后关联/后果，玩家须靠游戏"
                "自行揭开）：\n"
                f"{do_not_reveal}\n"
                + party_block + seen_block + turn_block + checks_block + location_block +
                "\n判定标准——**只拦「点破真相」，不拦「亲历现象」**：\n"
                "· 违规 = 旁白**命名、点破或解释**了上述隐藏真相：直接说出它是什么/是谁/为何发生/"
                "将导致什么；或让角色的内心「已然明白/认出」了这层真相（等于把答案塞进玩家脑子）；"
                "或以总结、暗示让玩家实质上得知了本该自己查明的因果。\n"
                "· **不违规** = 如实描写角色**正在亲眼目睹/亲耳所闻/亲身感受**的感官现象本身——"
                "哪怕它正是某个隐藏真相的外在显现。恐怖的观感、反常的景象、说不清的怪异、扭曲的画面，"
                "都是合法氛围；**绝不能因为它「指向」某个秘密就删掉**。玩家看得见的东西，就能写。\n\n"
                "此外无论上面是否为空，以下六类也算违规：\n"
                "1. 用【标题】加项目符号列表的「汇报体」总结状态/进展/待触发条件，而非自然叙事；\n"
                "2. 旁白里出现了 flag 名、线索/NPC 的内部 id、JSON 字段名等技术性标识；\n"
                "3. 重复、转述或文学化重演【本轮已展示消息】中的玩家/队友台词或动作；\n"
                "4. 替玩家/队友做了**有后果的事**：说出台词；移动或改变所处位置；取用、交出、"
                "使用物品；发起攻击；替他们做出判断、决定或领悟（「他认出这是…」「他决定先不点破」）；"
                "或替他们补一次没发生过的观察（「他方才就注意到了」——那等于跳过检定直接把信息给了他）。"
                "把玩家宣言的一个动作擅自扩写成一串新动作（只说「听听声音」却写成掏纸笔临摹、叩墙、"
                "逐寸摸索）同样算。\n"
                "   **被检定授权过的动作不算**：本轮掷过的检定（若上面列了），写角色为完成它"
                "做了什么、以及因此看到或得到了什么，都是在叙述裁定结果，不是代演——检定通过却"
                "不许写出所得，等于白掷。只有**没有对应检定**的信息获取，才算「替他补一次没发生"
                "过的观察」。\n"
                "   **下列不算代演，一律不要改**：伴随语气的姿态与小动作（摇一摇扇子、皱眉、屏住呼吸、"
                "指节收紧）、环境作用在角色身上的感官与生理反应（凉意顺着指腹爬上来、耳鸣、汗毛立起）、"
                "NPC 主动对角色作出的反应。判据是**有没有后果**——不改变位置、不产生新信息、"
                "不做出承诺的细节一律放行；它们是叙事的血肉，删掉只会把旁白逼成环境说明书。\n"
                "5. 同一条旁白内部出现两版相同或高度相似的句段，像写到一半重新起笔。\n"
                "6. 位置越界：旁白把玩家/队伍写成已经到达、进入或在【系统确定的当前位置硬约束】"
                "以外的场景中活动，或提前描写该场景的环境、NPC 与玩家所见所闻。玩家只是提到/"
                "打算去某地不等于已经到达；只有系统位置真的切换后才可以叙述那里的见闻。\n\n"
                f"待检查的旁白：\n{narration}\n\n"
                '不违规时只返回 {"violated": false}，不要回填旁白；\n'
                '违规时返回 {"violated": true, "reason": "简述违规之处", '
                '"corrected_narration": "改写后的旁白——删掉泄密、输入回显、玩家代演、位置越界和后写的重复版本；'
                '保留环境的新后果、NPC 反应/台词、角色可客观感知的现象与氛围，尽量少改动、不改文风"}\n'
                "只输出 JSON。"
            ),
        },
    ]


async def validate_turn_narration(
    llm: Any, plan: TurnPlan | None, narration: str, seen_context: str = "",
    turn_inputs: str = "", on_start: Callable[[], None] | None = None,
    party_names: Iterable[str] | None = None, location_context: str = "",
    settled_checks: str = "",
) -> TurnValidation | None:
    """校验一段已生成的旁白是否违反本轮裁定计划的硬约束，违反则给出改写版本。

    只对『落库/持久化的文本』生效——无法收回已经流式广播出去的内容，但能防止违规
    内容永久留在会话记录里（重连、其他玩家、复盘都会看到落库版本）。
    校验失败（无 LLM / 解析出错 / 调用异常）一律放行原文，不阻塞跑团。

    ``on_start`` 在**确定要真的调一次 LLM 时**才触发（短路掉的绝大多数轮次不触发），
    调用方据此告诉玩家这段静默在做什么——这一步发生在叙事流已经停下之后，不说明的话
    界面上只剩一个不动的脉冲点。
    """
    if plan is None or llm is None or not narration.strip():
        return None
    if not _looks_suspicious(narration, plan, turn_inputs, location_context):
        return None
    if on_start is not None:
        on_start()

    messages = build_validator_messages(
        plan, narration, seen_context, turn_inputs, party_names=party_names,
        location_context=location_context, settled_checks=settled_checks,
    )
    try:
        # 不设 max_tokens 硬上限：推理类模型的 reasoning 会占输出预算，硬上限会把 JSON 截成半截
        # 字符串（线上「Unterminated string」正是如此）。交服务端默认上限，complete 已内部流式。
        raw = await llm.complete(
            messages,
            temperature=0,
            response_format={"type": "json_object"},
        )
    except Exception:
        logger.exception("KP 回合校验器调用失败，按放行处理")
        return None

    # 稳健抠 JSON（剥围栏 / 夹带文字 / 已是 dict），比裸 json.loads 抗造；抠不出按放行处理。
    data = _extract_json_object(raw)
    if data is None:
        logger.warning("KP 回合校验器输出无法解析，按放行处理：%s", str(raw)[:200])
        return None
    try:
        result = TurnValidation.model_validate(data)
    except ValidationError as exc:
        logger.warning("KP 回合校验器输出不符合 schema，按放行处理：%s", exc)
        return None

    if result.violated and not result.corrected_narration.strip():
        # 兜底：模型判定违规却没给改写文本时，别把旁白整段清空
        result.corrected_narration = narration
    return result
