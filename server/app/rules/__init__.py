"""规则包入口。业务层只从这里取规则：``ruleset_for`` / ``get_system``。

``app.rules.coc`` 等具体规则系统的模块是组件实现，不对 ``app/rules`` 以外开放
（ruff banned-api 守边界，见 pyproject）。
"""

import app.rules.coc  # noqa: F401 — 触发注册
from app.rules import house_rules  # noqa: F401
from app.rules.kernel import (  # noqa: F401
    Ruleset,
    RulesetError,
    get_system,
    list_systems,
    ruleset_for,
)
