"""全局测试夹具。"""

from __future__ import annotations

import pytest

from app.api import ai_settings


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
