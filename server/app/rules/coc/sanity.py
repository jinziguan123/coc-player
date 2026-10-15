"""理智组件的门面：SAN 检定与临时疯狂口径。

实现分别在 :mod:`checks`（结算）与 :mod:`options`（口径的人话）。这里按名字现查再转发，
不在导入时拷贝函数引用——测试对 ``checks`` 打的桩照样生效。
"""

from __future__ import annotations

from app.rules.coc import checks
from app.rules.coc import options as _options


def san_check(*args, **kwargs):
    return checks.san_check(*args, **kwargs)


def insanity_trigger_text(options: _options.CocRuleOptions | None = None) -> str:
    return _options.insanity_trigger_text(options or _options.DEFAULT_OPTIONS)
