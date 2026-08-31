from datetime import datetime

from sqlalchemy import Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class LanPeer(Base):
    """局域网接入名册：房主允许哪些客户端连自己的后端。

    为什么需要它：内置直连那边，陌生人要在门口等房主点头（``src-tauri/src/netlink/
    roster.rs``），而局域网这边只有一个总开关——开了，同网段任何设备都能连。同样是
    「让人进来」，一边逐个批、一边整段放行，这个不对称没有道理。ADR-001 说的可信局域网
    是「相信朋友不捣乱」，不是「相信这个网段上的每一台设备」——咖啡馆、宿舍、公司网里
    还坐着别人。

    **按 X-Player-Token 认人，不是验人。** token 是客户端自造的明文串，谁拿到谁就是你
    （见 ``apps/web/src/api/client.ts`` 的说明）。所以这张表挡的是「不请自来的陌生
    设备」，挡不住存心伪造的人——那需要 TLS 与账号体系，见 ADR-007 的未决项。按 IP 认
    更差：DHCP 会换、同机多浏览器分不开，而 token 恰好是「一个客户端」的粒度，也正是
    席位归属用的那个标识，房主因此能把「谁在线」和「谁占着哪个席位」对上。

    明文存与 ``session_participants.owner_token`` 一致；哈希掉能让库泄露时少一处明文，
    但席位表那边已经是明文，泄露面不变，反而失去和席位对账的能力。
    """

    __tablename__ = "lan_peers"

    token: Mapped[str] = mapped_column(primary_key=True)
    """客户端的 X-Player-Token。"""

    status: Mapped[str] = mapped_column(default="pending", index=True)
    """pending（门口等着）/ approved（放行）/ rejected（拒过或吊销过）。

    拒绝与吊销都归到 rejected 而不是删记录：删了的话对方下次请求又是「陌生设备」，
    重新排到门口，房主被反复打扰。留着才拒得住。
    """

    label: Mapped[str] = mapped_column(default="")
    """房主起的备注名。"""

    claimed_label: Mapped[str] = mapped_column(default="")
    """对方自报的名字。**不可信**，界面必须表述成「自称」——谁都能这么叫自己。"""

    last_addr: Mapped[str] = mapped_column(default="")
    """最后一次请求的来源地址，帮房主认人（「那台 .17 是我的笔记本」）。"""

    first_seen: Mapped[datetime] = mapped_column(server_default=func.now())
    last_seen: Mapped[datetime] = mapped_column(server_default=func.now())
    """最后一次请求的时间。在线与否就按它算——HTTP 本来无连接，与其维护一张连接表，
    不如用「最近还在说话」来判断，直连客人的请求同样经过这里，一套口径覆盖两种接入。"""

    note: Mapped[str] = mapped_column(Text, default="")
    """预留：房主给这位写的备忘。"""
