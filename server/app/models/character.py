from typing import Optional

from sqlalchemy import JSON, Enum, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class Character(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "characters"

    name: Mapped[str] = mapped_column()
    module_id: Mapped[Optional[str]] = mapped_column(ForeignKey("modules.id"), nullable=True)
    rule_system: Mapped[str] = mapped_column(Enum("coc", "dnd", name="rule_system"))
    is_player: Mapped[bool] = mapped_column(default=True)
    # 角色归属于某玩家 token（阶段 2 联机：带角色入场认领席位）；AI 角色为 None
    owner_token: Mapped[Optional[str]] = mapped_column(nullable=True, index=True)
    # 参战副本指回客人库里的原件。有值即表示「这是一份副本」。
    #
    # **刻意不建外键**：原件在客人自己的库里，这个 id 在房主库里根本不存在——它是
    # 跨库标识，不是引用完整性约束。客人据此把本局的 HP/SAN/成长/物品写回原件。
    origin_character_id: Mapped[Optional[str]] = mapped_column(nullable=True, index=True)
    base_attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    skills: Mapped[dict] = mapped_column(JSON, default=dict)
    system_data: Mapped[dict] = mapped_column(JSON, default=dict)
    backstory: Mapped[str] = mapped_column(Text, default="")
    #: 头像图片路径（与 NPC 立绘同一套 image_store 落盘形态）。
    #: 为空是**正常状态**，不是缺陷——前端回落到「姓名首字纹章」。
    avatar_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    #: 模组经历：跑完一个本就归档一条，形如 {"module_id", "module_title", "ending_name",
    #: "session_id", "at", "survived", "story"}。story 是结局后 LLM 写的第三人称小传；
    #: 其余元数据供档案卡计数、去重（同一会话只归档一次）与排序。
    experiences: Mapped[list] = mapped_column(JSON, default=list)
    # 角色状态（应用层概念，取值见 app/rules/coc/status.py）：正常/重伤/昏迷/死亡/
    # 临时疯狂/不定期疯狂/永久疯狂。用普通字符串，避免 DB 枚举 CHECK 约束阻挡新增状态。
    status: Mapped[str] = mapped_column(Text, default="active")
