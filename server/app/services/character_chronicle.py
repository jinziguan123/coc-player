"""模组经历归档：一局落幕后，给每个玩家角色写一段小传存进角色卡。

**为什么要有它。** 此前一场本跑完，角色卡和开局时长得一模一样——玩家投入几十轮攒下的
东西，在卡上不留任何痕迹。下次拿这张卡开新本，它还是个白纸新人。

写的是**第三人称小传**而不是流水账：角色卡是拿来给人看的，「他在渡口截下了那封信，却
没能救回当铺老板」比「模组：渡口来信 / 结局：真相大白 / 存活：是」有分量得多。结构化
元数据照样存，但那是给档案卡计数、去重、排序用的，不是给人读的。

**走主模型**：这段文字会原样摆在玩家面前，按 llm_factory 的既定判据（产出是否直接面向
玩家）就该走主模型，而不是结构化副任务那档快模型。

**每人一份、各写各的**：同一局里每个角色的经历不同——有人活着有人死了，有人查到了真相
有人蒙在鼓里。共用一段总结会把这些差别抹平。

全程 fail-open：归档失败只记日志，绝不影响已经结束的会话。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.ai.llm_factory import get_llm
from app.models.character import Character
from app.models.module import Module
from app.models.session import GameSession
from app.services import session_service, world_memory

logger = logging.getLogger(__name__)

#: 喂给小传的剧情素材上限（估算字符）。滚动摘要本身已是浓缩产物，再截一刀防超长。
MAX_STORY_CHARS = 6000
#: 小传正文长度指导，写进提示词。太长会把角色卡撑爆，太短又讲不出一段经历。
CHRONICLE_LENGTH_HINT = "150 到 300 字"


def _chronicle_messages(
    char: Character, module: Module, story: str, ending_name: str, status: str,
) -> list[dict]:
    fate = {
        "dead": "这名调查员在本局中死亡",
        "insane_permanent": "这名调查员在本局中永久疯狂",
    }.get(status, "这名调查员活着走出了这个故事")
    return [
        {
            "role": "system",
            "content": (
                "你是 TRPG 的记录者。给一名调查员写一段**第三人称**的经历小传，"
                "记进他的角色卡，供他本人日后回看、也供他带着这张卡进入下一个故事。"
                "只输出小传正文，不要标题、不要分点、不要任何前后说明。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"要求：\n"
                f"- 篇幅 {CHRONICLE_LENGTH_HINT}，一到两段，紧凑成文。\n"
                "- **只写这名调查员的视角**：他做过什么、见证了什么、付出了什么代价、"
                "留下了什么未了之事。别人的功劳不要写到他头上。\n"
                "- 用具体的事写，不要空泛评价（写「他在渡口截下了那封信」，"
                "不要写「他表现得很勇敢」）。\n"
                "- 结尾落在他此刻的处境或心境上，不要写成总结陈词。\n"
                "- 不要出现「模组」「玩家」「检定」「骰子」这类场外词汇——这是他的人生，不是一局游戏。\n\n"
                f"【调查员】{char.name}"
                + (f"（{(char.system_data or {}).get('occupation')}）"
                   if (char.system_data or {}).get("occupation") else "")
                + f"\n【他的结局】{fate}\n"
                f"【故事】{module.title}\n"
                + (f"【本局落点】{ending_name}\n" if ending_name else "")
                + f"\n【这一局发生了什么】\n{story}\n\n请写他的小传："
            ),
        },
    ]


def already_archived(char: Character, session_id: str) -> bool:
    """该角色是否已归档过这一局（结束流程可能被重入，不能重复记账）。"""
    return any(
        isinstance(e, dict) and e.get("session_id") == session_id
        for e in (char.experiences or [])
    )


def append_experience(db: Session, char: Character, entry: dict) -> None:
    """把一条经历追加进角色卡。

    JSON 列必须**整体赋新值**才会被 SQLAlchemy 视为脏——就地 append 不会触发更新，
    提交后看着成功、实际什么都没写进去。
    """
    char.experiences = [*(char.experiences or []), entry]
    db.commit()


async def archive_session(db: Session, session_id: str) -> int:
    """给本局**所有上过桌的角色**归档经历，返回成功归档的份数。

    在**收场白生成之后**调用：那时故事真的讲完了，滚动摘要也已收进最后一批事件，
    素材最全。全程 fail-open。

    从前这里跳过 `is_player=False` 的卡，于是 AI 队友走完整个模组也不留一行记录。
    可它和玩家角色一样在场、一样掷骰、一样可能死在里面——而且那个标志本就只是
    「建卡时点了哪个按钮」，与谁在演它无关（谁演看的是席位的 role）。同一张卡这局
    由 AI 驱动、下局被真人认领，经历却因此断掉一截，说不通。
    """
    try:
        game_session = db.get(GameSession, session_id)
        if game_session is None:
            return 0
        module = db.get(Module, game_session.module_id) if game_session.module_id else None
        if module is None:
            return 0
        chars = session_service.get_party_members(db, session_id)
        if not chars:
            return 0

        ws = game_session.world_state or {}
        story = world_memory.story_summary_text(ws)[:MAX_STORY_CHARS]
        if not story.strip():
            # 没有滚动摘要（短局）→ 用事件正文兜底，否则小传无米下炊
            events = session_service.get_session_events(db, session_id, limit=0)
            story = "\n".join(
                f"{e.actor_name or ''}：{(e.content or '')[:200]}"
                for e in events[-60:]
                if e.event_type in ("narration", "dialogue", "action") and (e.content or "").strip()
            )[:MAX_STORY_CHARS]
        if not story.strip():
            return 0
        ending_name = str((ws.get("ending_reached") or {}).get("name") or "")
        at = datetime.now(timezone.utc).isoformat()

        llm = get_llm()
        archived = 0
        for char in chars:
            if already_archived(char, session_id):
                continue
            try:
                raw = await llm.complete(
                    _chronicle_messages(char, module, story, ending_name, char.status),
                    temperature=0.7,
                )
            except Exception:
                logger.exception("角色小传生成失败（跳过该角色）：character=%s", char.id)
                continue
            text = (raw or "").strip() if isinstance(raw, str) else ""
            if not text:
                continue
            append_experience(db, char, {
                "session_id": session_id,
                "module_id": module.id,
                "module_title": module.title,
                "ending_name": ending_name,
                "at": at,
                "survived": char.status not in ("dead", "insane_permanent"),
                "final_status": char.status,
                "story": text,
            })
            archived += 1
            _mirror_to_origin(db, char, session_id)
        if archived:
            logger.info("模组经历归档：session=%s 共 %s 名调查员", session_id, archived)
        return archived
    except Exception:
        logger.exception("模组经历归档失败（忽略）：session=%s", session_id)
        return 0


def _mirror_to_origin(db: Session, copy: Character, session_id: str) -> None:
    """参战副本的经历写回客人自己的原件。

    客人入座时会在房主机器上留一份副本（origin_character_id 指回原件）。经历只写在副本上
    的话，客人回到自己的库里看到的还是那张白纸卡——这一局对他等于没发生过。原件不在本库时
    静默跳过（跨库标识，本就可能查不到）。
    """
    origin_id = getattr(copy, "origin_character_id", None)
    if not origin_id:
        return
    try:
        origin = db.get(Character, origin_id)
        if origin is None or already_archived(origin, session_id):
            return
        entry = (copy.experiences or [])[-1]
        append_experience(db, origin, dict(entry))
    except Exception:
        logger.exception("经历回写原件失败（忽略）：origin=%s", origin_id)
