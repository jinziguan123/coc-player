"""characters.avatar_url / experiences：角色头像与模组经历

此前角色卡只有「当下这一张表」：属性、技能、背景，没有任何东西记得它经历过什么。
一场本跑完，角色卡和开局时长得一模一样——玩家投入几十轮攒下的东西，在卡上不留痕迹。

- avatar_url：头像图片路径（沿用 image_store 的落盘路径形态，与 NPC 立绘同一套）。
  为空 = 回落到现有的「姓名首字纹章」，不是缺陷状态。
- experiences：模组经历列表，每条形如
  {"module_id", "module_title", "ending_name", "session_id", "at",
   "survived", "story"}。story 是结局后由 LLM 写的第三人称小传；其余是最小
  元数据——档案卡要靠它显示「已归档 N 篇」、去重（同一会话只归档一次）、按时间排序。

两列都可空、默认空，存量角色卡无需数据迁移：没有头像就是首字纹章，没有经历就是新人。

Revision ID: f5c1d83b7e24
Revises: e4b9c72a6d13
Create Date: 2026-08-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f5c1d83b7e24"
down_revision: Union[str, Sequence[str], None] = "e4b9c72a6d13"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("characters")}


def upgrade() -> None:
    # 幂等：版本号可能因历史漂移被回拨到本迁移之前，而列其实已经建好
    # （见 tests/test_migrations.py 的 identity_schema_repair 用例）。
    cols = _columns()
    if "avatar_url" not in cols:
        op.add_column("characters", sa.Column("avatar_url", sa.Text(), nullable=True))
    if "experiences" not in cols:
        op.add_column(
            "characters",
            sa.Column("experiences", sa.JSON(), nullable=False, server_default="[]"),
        )


def downgrade() -> None:
    cols = _columns()
    if "experiences" in cols:
        op.drop_column("characters", "experiences")
    if "avatar_url" in cols:
        op.drop_column("characters", "avatar_url")
