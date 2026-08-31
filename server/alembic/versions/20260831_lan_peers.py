"""局域网接入名册 lan_peers

局域网此前只有一个总开关：开了，同网段任何设备都能连。而内置直连那边陌生人要在门口
等房主点头。同样是「让人进来」，一边逐个批、一边整段放行。这张表把逐个批的模型补到
局域网侧，顺带承载「谁还在线」——last_seen 就够了，不必另建连接表。

Revision ID: f7c2e4a9b8d1
Revises: f7b2d4e6a8c1
Create Date: 2026-08-31
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f7c2e4a9b8d1"
down_revision: Union[str, Sequence[str], None] = "f7b2d4e6a8c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "lan_peers" in inspector.get_table_names():
        return
    op.create_table(
        "lan_peers",
        sa.Column("token", sa.String(), primary_key=True),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("label", sa.String(), nullable=False, server_default=""),
        sa.Column("claimed_label", sa.String(), nullable=False, server_default=""),
        sa.Column("last_addr", sa.String(), nullable=False, server_default=""),
        sa.Column(
            "first_seen", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "last_seen", sa.DateTime(), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
    )
    op.create_index("ix_lan_peers_status", "lan_peers", ["status"])

    # 已经在席位上的人直接算批准过。这道闸默认拒绝（fail closed 是对的），但升级不该
    # 表现成「朋友昨天还在玩，今天全被挡在门外」——房主让他们上过桌，就是批准过。
    # 房主自己的 token 也在里面，无害：本机根本不过这道闸，而他哪天从别的机器连回来
    # 也照样该放行。
    op.execute(
        """
        INSERT OR IGNORE INTO lan_peers (token, status, label, claimed_label, last_addr, note)
        SELECT DISTINCT p.owner_token, 'approved', COALESCE(c.name, ''), '', '', '席位归属自动批准'
        FROM session_participants p
        LEFT JOIN characters c ON c.id = p.character_id
        WHERE p.owner_token IS NOT NULL AND p.owner_token <> ''
        """
    )


def downgrade() -> None:
    op.drop_index("ix_lan_peers_status", table_name="lan_peers")
    op.drop_table("lan_peers")
