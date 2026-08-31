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
    # 建表与回填分开判断。合在一起写成「表在就整个 return」的话，只要表因为任何原因先
    # 存在了（开发时中途重载、手工建过），回填就被静默跳过——而回填恰恰是这次升级里
    # 唯一会影响用户的部分：跳过了，他的朋友第二天全被挡在门外，且不会有任何报错。
    inspector = sa.inspect(op.get_bind())
    if "lan_peers" not in inspector.get_table_names():
        _create(inspector)
    _backfill()


def _create(inspector: sa.Inspector) -> None:
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


def _backfill() -> None:
    """已经在席位上的人直接算批准过。

    这道闸默认拒绝（fail closed 是对的），但升级不该表现成「朋友昨天还在玩，今天全被
    挡在门外」——房主让他们上过桌，就是批准过。房主自己的 token 也在里面，无害：本机
    根本不过这道闸，而他哪天从别的机器连回来也照样该放行。

    只在名册还空着时回填。房主清空过名册的话那是他的决定，不该被一次升级推翻；而
    `INSERT OR IGNORE` 也保证了重复执行不会覆盖任何已有的表态。
    """
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT COUNT(*) FROM lan_peers")).scalar():
        return
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
