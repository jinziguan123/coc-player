"""CoC 村规：各组件参数的合成。

**读时覆盖，绝不写回**。引擎每次结算现读这份配置，不把它烙进角色卡、事件或任何
已落库的数据——关掉村规就立刻回到 RAW，不留残迹。这一条抄自 Foundry 的 Active
Effects：它把改动应用在角色数据的副本上而非原件上，所以效果一撤销，原值自然回来。

**缺省即 RAW**：每个字段的默认值都写在它所属组件的 ``XxxConfig`` 里，等于规则书原文。
存量存档的 ``rule_options`` 是空 dict，走 :data:`DEFAULT_OPTIONS`，不需要数据迁移。

参数本身不在这里定义——见 ``components/`` 下各组件文件。这里只把它们合成一个对象，
供规则内核解析、缓存与注入（``Ruleset`` 注入给各组件函数的就是它）。
"""

from __future__ import annotations

from app.rules.coc.components.check import CheckConfig
from app.rules.coc.components.growth import GrowthConfig
from app.rules.coc.components.luck_reroll import LuckRerollConfig
from app.rules.coc.components.luck_spend import LuckSpendConfig
from app.rules.coc.components.sanity import SanityConfig
from app.rules.coc.components.wound import WoundConfig
from app.rules.config import parse_lenient


class CocRuleOptions(
    CheckConfig, LuckSpendConfig, LuckRerollConfig, WoundConfig, SanityConfig, GrowthConfig,
):
    """一局（或一个模组）生效的完整村规：各组件参数的合成。

    字段默认值 = RAW，改一项只影响那一项。只能经 :func:`from_dict`（或 Ruleset）得到——
    界面与存档来的值都要先过一遍白名单与区间。
    """


DEFAULT_OPTIONS = CocRuleOptions()


def from_dict(raw: dict | CocRuleOptions | None) -> CocRuleOptions:
    """把外部 dict 规范化成 :class:`CocRuleOptions`：认识的键才要，值一律钳进合法区间。

    未知键直接丢弃（旧前端提交的、或以后删掉的字段不会把这里搞崩）；类型不对的值退回默认。
    """
    return parse_lenient(CocRuleOptions, raw, DEFAULT_OPTIONS)
