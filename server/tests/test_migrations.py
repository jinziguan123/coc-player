"""启动自动迁移的回归：run_migrations 能把空库一路升到最新 schema。

顺带守住迁移链可用（任何一个迁移脚本坏掉都会让本测试失败）。
"""

import sqlite3

from app import database
from app.config import settings


def test_run_migrations_builds_full_schema(tmp_path, monkeypatch):
    db_file = tmp_path / "fresh.db"
    monkeypatch.setattr(settings, "db_path", db_file)

    database.run_migrations()

    con = sqlite3.connect(db_file)
    try:
        tables = {
            r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        con.close()

    # 迁移链跑通的标志：alembic 版本表 + 本次新增的 RAG 两表 + 既有核心表都在
    assert "alembic_version" in tables
    assert "rulebooks" in tables
    assert "rule_chunks" in tables
    assert "module_chunks" in tables
    assert "modules" in tables
    assert "game_sessions" in tables
    # 战斗/追逐/运行统计/战报拆表（20260814）：四张独立表在迁移链里
    assert "combat_states" in tables
    assert "chase_states" in tables
    assert "session_stats" in tables
    assert "session_recaps" in tables

    # Handouts 迁移（20260703）：modules 表带 handouts JSON 列
    con = sqlite3.connect(db_file)
    try:
        module_cols = {r[1] for r in con.execute("PRAGMA table_info(modules)")}
    finally:
        con.close()
    assert "handouts" in module_cols
    # 幕后真相迁移（20260719）：modules 表带 truth TEXT 列
    assert "truth" in module_cols

    con = sqlite3.connect(db_file)
    try:
        session_cols = {
            r[1] for r in con.execute("PRAGMA table_info(game_sessions)")
        }
        participant_cols = {
            r[1] for r in con.execute("PRAGMA table_info(session_participants)")
        }
    finally:
        con.close()
    assert {"host_token", "identity_version"} <= session_cols
    assert "identity_version" in participant_cols


def test_run_migrations_is_idempotent(tmp_path, monkeypatch):
    db_file = tmp_path / "again.db"
    monkeypatch.setattr(settings, "db_path", db_file)
    database.run_migrations()
    database.run_migrations()  # 第二次为 no-op，不应抛错


def test_identity_schema_repair_fixes_already_stamped_database(tmp_path, monkeypatch):
    """版本号已到旧身份迁移、实际缺列时，后续修复迁移必须按 schema 补齐。"""
    db_file = tmp_path / "identity-drift.db"
    monkeypatch.setattr(settings, "db_path", db_file)
    database.run_migrations()

    con = sqlite3.connect(db_file)
    try:
        con.execute("ALTER TABLE game_sessions DROP COLUMN identity_version")
        con.execute(
            "UPDATE alembic_version SET version_num = 'd4f7a9c2e1b3'"
        )
        con.commit()
    finally:
        con.close()

    database.run_migrations()

    con = sqlite3.connect(db_file)
    try:
        columns = {
            row[1] for row in con.execute("PRAGMA table_info(game_sessions)")
        }
    finally:
        con.close()
    assert "identity_version" in columns


def test_noop_migration_creates_no_backup(tmp_path, monkeypatch):
    """已是最新时 run_migrations 为 no-op，不应留下备份文件（避免每次启动都堆备份）。"""
    db_file = tmp_path / "noop.db"
    monkeypatch.setattr(settings, "db_path", db_file)
    database.run_migrations()
    database.run_migrations()
    assert not list(tmp_path.glob("noop.db.bak-*"))


def test_migration_backs_up_before_upgrading(tmp_path, monkeypatch):
    """有待应用迁移时，升级前先自动备份整库；升级后库到达最新。"""
    from alembic import command

    db_file = tmp_path / "up.db"
    monkeypatch.setattr(settings, "db_path", db_file)
    database.run_migrations()  # 建到最新
    # 回退一格，制造「有待应用迁移」的状态
    command.downgrade(database._alembic_config(), "-1")
    cur_before, head = database.migration_status()
    assert cur_before != head

    database.run_migrations()  # 应先备份再升级
    backups = list(tmp_path.glob("up.db.bak-*"))
    assert backups, "迁移前应生成备份"
    cur_after, head2 = database.migration_status()
    assert cur_after == head2  # 已升到最新


def test_combat_state_migration_backfills_old_saves(tmp_path, monkeypatch):
    """旧存档里 world_state.combat 在升级时搬进 combat_states，并从 world_state 移除（ADR-003 第 5 条）。"""
    import json

    from alembic import command

    db_file = tmp_path / "combat-backfill.db"
    monkeypatch.setattr(settings, "db_path", db_file)
    database.run_migrations()
    # 回退到拆表迁移之前（固定版本，而非相对步数），模拟「旧库：战斗态还在 world_state 里」
    command.downgrade(database._alembic_config(), "f5c1d83b7e24")

    con = sqlite3.connect(db_file)
    try:
        con.execute(
            "INSERT INTO game_sessions (id, module_id, status, world_state) "
            "VALUES (?, ?, ?, ?)",
            (
                "s1",
                "m1",
                "active",
                json.dumps({
                    "combat": {"active": True, "round": 3, "initiative": []},
                    "flags": {"door_open": True},
                }),
            ),
        )
        con.commit()
    finally:
        con.close()

    database.run_migrations()  # 应用本迁移：回填 + 移除 combat 键

    con = sqlite3.connect(db_file)
    try:
        row = con.execute(
            "SELECT state, version FROM combat_states WHERE session_id = 's1'"
        ).fetchone()
        ws_raw = con.execute(
            "SELECT world_state FROM game_sessions WHERE id = 's1'"
        ).fetchone()
    finally:
        con.close()

    assert row is not None
    assert json.loads(row[0]) == {"active": True, "round": 3, "initiative": []}
    assert row[1] == 1
    ws = json.loads(ws_raw[0])
    assert "combat" not in ws
    assert ws["flags"] == {"door_open": True}


def test_chase_state_migration_backfills_old_saves(tmp_path, monkeypatch):
    """旧存档里 world_state.chase 在升级时搬进 chase_states，并从 world_state 移除。"""
    import json

    from alembic import command

    db_file = tmp_path / "chase-backfill.db"
    monkeypatch.setattr(settings, "db_path", db_file)
    database.run_migrations()
    # 回退到拆表迁移之前（固定版本），模拟「旧库：追逐态还在 world_state 里」
    command.downgrade(database._alembic_config(), "f5c1d83b7e24")

    con = sqlite3.connect(db_file)
    try:
        con.execute(
            "INSERT INTO game_sessions (id, module_id, status, world_state) "
            "VALUES (?, ?, ?, ?)",
            (
                "s1",
                "m1",
                "active",
                json.dumps({
                    "chase": {"active": True, "round": 2, "gap": 1},
                    "flags": {"door_open": True},
                }),
            ),
        )
        con.commit()
    finally:
        con.close()

    database.run_migrations()  # 应用 combat + chase 迁移：回填 + 移除

    con = sqlite3.connect(db_file)
    try:
        row = con.execute(
            "SELECT state, version FROM chase_states WHERE session_id = 's1'"
        ).fetchone()
        ws_raw = con.execute(
            "SELECT world_state FROM game_sessions WHERE id = 's1'"
        ).fetchone()
    finally:
        con.close()

    assert row is not None
    assert json.loads(row[0]) == {"active": True, "round": 2, "gap": 1}
    assert row[1] == 1
    ws = json.loads(ws_raw[0])
    assert "chase" not in ws
    assert ws["flags"] == {"door_open": True}


def test_session_stats_migration_backfills_old_saves(tmp_path, monkeypatch):
    """旧存档里 usage/rag 三个键在升级时搬进 session_stats，并从 world_state 移除。"""
    import json

    from alembic import command

    db_file = tmp_path / "stats-backfill.db"
    monkeypatch.setattr(settings, "db_path", db_file)
    database.run_migrations()
    command.downgrade(database._alembic_config(), "f5c1d83b7e24")

    con = sqlite3.connect(db_file)
    try:
        con.execute(
            "INSERT INTO game_sessions (id, module_id, status, world_state) "
            "VALUES (?, ?, ?, ?)",
            (
                "s1",
                "m1",
                "active",
                json.dumps({
                    "session_usage": {"total_tokens": 999, "calls": 7},
                    "turn_usage": {"prompt_tokens": 100},
                    "rag_stats": {"totals": {"calls": 3}},
                    "flags": {"door_open": True},
                }),
            ),
        )
        con.commit()
    finally:
        con.close()

    database.run_migrations()  # 应用 combat/chase/session_stats 迁移：回填 + 移除

    con = sqlite3.connect(db_file)
    try:
        row = con.execute(
            "SELECT session_usage, turn_usage, rag_stats, version "
            "FROM session_stats WHERE session_id = 's1'"
        ).fetchone()
        ws_raw = con.execute(
            "SELECT world_state FROM game_sessions WHERE id = 's1'"
        ).fetchone()
    finally:
        con.close()

    assert row is not None
    assert json.loads(row[0]) == {"total_tokens": 999, "calls": 7}
    assert json.loads(row[1]) == {"prompt_tokens": 100}
    assert json.loads(row[2]) == {"totals": {"calls": 3}}
    assert row[3] == 1
    ws = json.loads(ws_raw[0])
    for key in ("session_usage", "turn_usage", "rag_stats"):
        assert key not in ws
    assert ws["flags"] == {"door_open": True}


def test_session_recaps_migration_backfills_old_saves(tmp_path, monkeypatch):
    """旧存档里 world_state.recaps 列表在升级时摊成 session_recaps 行，并从 world_state 移除。"""
    import json

    from alembic import command

    db_file = tmp_path / "recaps-backfill.db"
    monkeypatch.setattr(settings, "db_path", db_file)
    database.run_migrations()
    command.downgrade(database._alembic_config(), "f5c1d83b7e24")

    con = sqlite3.connect(db_file)
    try:
        con.execute(
            "INSERT INTO game_sessions (id, module_id, status, world_state) "
            "VALUES (?, ?, ?, ?)",
            (
                "s1",
                "m1",
                "active",
                json.dumps({
                    "recaps": [
                        {"title": "第一战", "up_to_seq": 5},
                        {"title": "第二战", "up_to_seq": 9},
                    ],
                    "flags": {"door_open": True},
                }),
            ),
        )
        con.commit()
    finally:
        con.close()

    database.run_migrations()  # 应用全部拆表迁移：回填 + 移除

    con = sqlite3.connect(db_file)
    try:
        rows = con.execute(
            "SELECT ordinal, entry FROM session_recaps "
            "WHERE session_id = 's1' ORDER BY ordinal"
        ).fetchall()
        ws_raw = con.execute(
            "SELECT world_state FROM game_sessions WHERE id = 's1'"
        ).fetchone()
    finally:
        con.close()

    assert [r[0] for r in rows] == [0, 1]
    assert [json.loads(r[1])["title"] for r in rows] == ["第一战", "第二战"]
    ws = json.loads(ws_raw[0])
    assert "recaps" not in ws
    assert ws["flags"] == {"door_open": True}


def test_turn_confirm_migration_moves_to_turn_state(tmp_path, monkeypatch):
    """旧存档里 world_state.turn_confirm 在升级时搬进 turn_state 列，并从 world_state 移除。"""
    import json

    from alembic import command

    db_file = tmp_path / "turn-confirm-backfill.db"
    monkeypatch.setattr(settings, "db_path", db_file)
    database.run_migrations()
    command.downgrade(database._alembic_config(), "f5c1d83b7e24")

    con = sqlite3.connect(db_file)
    try:
        con.execute(
            "INSERT INTO game_sessions (id, module_id, status, world_state) "
            "VALUES (?, ?, ?, ?)",
            (
                "s1",
                "m1",
                "active",
                json.dumps({"turn_confirm": {"c1": True}, "flags": {"door_open": True}}),
            ),
        )
        con.commit()
    finally:
        con.close()

    database.run_migrations()  # 应用全部拆表迁移

    con = sqlite3.connect(db_file)
    try:
        ts_raw, ws_raw = con.execute(
            "SELECT turn_state, world_state FROM game_sessions WHERE id = 's1'"
        ).fetchone()
    finally:
        con.close()

    assert json.loads(ts_raw) == {"c1": True}
    ws = json.loads(ws_raw)
    assert "turn_confirm" not in ws
    assert ws["flags"] == {"door_open": True}


def test_downgrade_scenario_rejected(tmp_path, monkeypatch):
    """库版本不在代码已知迁移链内（旧程序打开新库）时，拒绝迁移而非带病运行。"""
    import sqlite3

    import pytest

    db_file = tmp_path / "future.db"
    monkeypatch.setattr(settings, "db_path", db_file)
    database.run_migrations()
    # 伪造一个「未来版本号」写进 alembic_version，模拟旧程序遇到更新的库
    con = sqlite3.connect(db_file)
    try:
        con.execute("UPDATE alembic_version SET version_num = 'zzzz_future_rev'")
        con.commit()
    finally:
        con.close()
    with pytest.raises(RuntimeError, match="高于本程序已知"):
        database.run_migrations()
