"""升级到接入名册这一版时，已经在席位上的人要自动算批准过。

为什么单独测它：名册默认拒绝是对的，但升级不该表现成「朋友昨天还在玩，今天全被挡在
门外」。而回填一旦被跳过，**不会有任何报错**——房主只会在下次联机时发现谁都进不来，
再去猜是哪儿的问题。这种静默失效值得钉死。

（写这个测试的直接原因：开发时 dev server 带 --reload，在回填那段写完之前就已经把
版本记到了 head，于是本机库建了表却没回填。当时的迁移写成「表在就整个 return」，
真让用户碰上同样的时序，后果一模一样。）
"""

from __future__ import annotations

import pathlib

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

REVISION = "f7c2e4a9b8d1"
PREVIOUS = "f7b2d4e6a8c1"


def _config(db_path) -> Config:
    """程序化 Config，**不读 alembic.ini**——与 app.database._alembic_config 同一理由：
    env.py 会拿 ini 路径去 fileConfig()，那会以 disable_existing_loggers=True 重置整棵
    logging 树，把 app logger 的 handler 掀掉。后果不落在本文件，而是让同一次运行里所有
    断言日志输出的用例集体变成哑弹（实测：10 个）。"""
    server_dir = pathlib.Path(__file__).resolve().parent.parent
    cfg = Config()
    cfg.set_main_option("script_location", str(server_dir / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def _seed_seats(engine) -> None:
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO characters (id, name, rule_system, is_player, base_attributes,"
            " skills, system_data, backstory, status)"
            " VALUES ('c1', '陈守一', 'coc', 1, '{}', '{}', '{}', '', '')"
        ))
        for seat, (token, char) in enumerate([
            ("tok-host", "c1"), ("tok-friend", None), ("tok-friend", None),  # 同一人两个席位
        ]):
            conn.execute(
                text(
                    "INSERT INTO session_participants"
                    " (id, session_id, character_id, role, seat_order, is_primary, owner_token)"
                    " VALUES (:id, 's1', :char, 'human', :seat, 0, :token)"
                ),
                {"id": f"p{seat}", "char": char, "seat": seat, "token": token},
            )


def test_升级时把已经在席位上的人算作批准过(tmp_path):
    db = tmp_path / "upgrade.db"
    cfg = _config(db)
    command.upgrade(cfg, PREVIOUS)

    engine = create_engine(f"sqlite:///{db}")
    _seed_seats(engine)

    command.upgrade(cfg, REVISION)

    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT token, status, label FROM lan_peers ORDER BY token")
        ).all()
    engine.dispose()

    # 同一个 token 占两个席位只该出现一次
    assert [(r[0], r[1]) for r in rows] == [
        ("tok-friend", "approved"), ("tok-host", "approved"),
    ]
    # 认得出是谁：有角色的带上角色名
    assert dict((r[0], r[2]) for r in rows)["tok-host"] == "陈守一"


def test_表已存在但名册还空着时照样补上回填(tmp_path):
    """这正是本机踩到的那一下：表因为别的原因先存在了，回填却被跳过。

    迁移若写成「表在就整个 return」，这里就会静默地什么都不做——没有报错，房主要到
    下次联机时才发现谁都进不来。用 downgrade 模拟不了这个场景：降级会把表删掉。
    """
    db = tmp_path / "rerun.db"
    cfg = _config(db)
    command.upgrade(cfg, PREVIOUS)

    engine = create_engine(f"sqlite:///{db}")
    _seed_seats(engine)
    with engine.begin() as conn:      # 手工建表，模拟「表已存在但没回填」
        conn.execute(text(
            "CREATE TABLE lan_peers ("
            " token VARCHAR PRIMARY KEY,"
            " status VARCHAR NOT NULL DEFAULT 'pending',"
            " label VARCHAR NOT NULL DEFAULT '',"
            " claimed_label VARCHAR NOT NULL DEFAULT '',"
            " last_addr VARCHAR NOT NULL DEFAULT '',"
            " first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,"
            " last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,"
            " note TEXT NOT NULL DEFAULT '')"
        ))

    command.upgrade(cfg, REVISION)

    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM lan_peers")).scalar()
    engine.dispose()
    assert count == 2
