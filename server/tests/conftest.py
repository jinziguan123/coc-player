"""全局测试夹具。"""

from __future__ import annotations

import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.database as database
from app.api import ai_settings
from app.models.base import Base


@pytest.fixture(autouse=True)
def isolate_ai_settings(tmp_path, monkeypatch):
    """把 AI 配置文件指向临时路径，让测试与开发机上激活的模型彻底解耦。

    此前 ``SETTINGS_FILE`` 直指 ``server/ai_settings.json``：跑测试读的是开发者当下在用的
    那份配置。后果不是理论上的——上下文组装预算按激活模型的窗口自适应，滚动摘要的触发阈值
    又按预算算，于是「攒够 N 条事件就该浓缩」这类断言会随开发者换模型而红/绿。
    （实测：把 deepseek-v4 的窗口从 64K 订正为 1M 后，4 个摘要用例当场失败。）

    指向空目录 → ``load_active_profile()`` 返回 None → 窗口回落 65536 默认值，
    即这些用例被写下时的那套预算。要测特定窗口的行为，用例自行 monkeypatch。
    """
    monkeypatch.setattr(ai_settings, "SETTINGS_FILE", tmp_path / "ai_settings.json")


@pytest.fixture(autouse=True)
def isolate_dev_db(tmp_path, monkeypatch):
    """把 ``SessionLocal`` 指向一次性临时库，任何测试都别想写到开发库上。

    这不是假想的风险。上传任务 ``_run_upload_job`` 会一路跑到入库，而验证「走了哪条解析
    链路」的用例只打桩了解析函数、没管 SessionLocal——于是每跑一次全量测试，开发库里就
    多两条标题为「T」的空模组，攒到二十条才被看见。

    麻烦在于 ``SessionLocal`` 被各处 ``from app.database import SessionLocal`` 过，
    只改 ``app.database`` 上的那一个没用——已经拿到引用的模块照旧写真库。所以扫一遍
    ``sys.modules``，凡是持有同一个对象的模块属性一并换掉。

    用例自己 monkeypatch SessionLocal 的（如 db_factory 那批）不受影响：
    autouse 夹具先跑，用例随后的 setattr 覆盖它。
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'test-isolated.db'}")
    Base.metadata.create_all(engine)
    tmp_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    real = database.SessionLocal
    for mod in list(sys.modules.values()):
        if getattr(mod, "SessionLocal", None) is real:
            monkeypatch.setattr(mod, "SessionLocal", tmp_session, raising=False)
    yield
    engine.dispose()
