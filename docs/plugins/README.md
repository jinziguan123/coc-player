# 规则插件

规则插件是一段 Python 代码，用来给规则引擎增加一条**子规则**。它的写法和官方组件完全一样（官方组件见 `server/app/rules/coc/components/`），只是放在你自己机器上的插件目录里，应用启动时加载。

> **先看看房规文件能不能解决。** 调参数、增删改武器、职业、技能这类数据，用规则书页的「导出房规 / 导入房规」就够了。房规文件只含数据，导入时不会执行任何代码，发给别人也安全。只有需要改变规则的**行为**时，才需要写插件，比如"某种情况下多一颗惩罚骰"。

## 安全须知

插件以本应用的权限在你的电脑上运行，**没有沙箱**，和安装游戏 mod 一样，风险由安装它的人承担。只安装你信任来源的插件，安装前最好读一遍代码。

## 安装

1. 把插件文件（`xxx.py`，或一个带 `__init__.py` 的目录）放进插件目录：
   - 源码运行：`server/plugins/`
   - 打包版：应用数据目录下的 `plugins/`。macOS 是 `~/Library/Application Support/TRPGPlayer/plugins/`。
   - 也可以用环境变量 `PLUGINS_DIR` 指定别的位置。
2. 重启应用。
3. 到规则书页的「规则插件」卡片，确认插件已加载。没加载的话，卡片上会写明原因。
4. 在侧栏「规则」页对应的分区里**打开**插件提供的规则（卡片上标「插件」）。插件组件一律是可选的，装上之后默认不生效。

## 写法

参考同目录下的 [`wound_penalty.py`](wound_penalty.py)。一个插件模块需要声明以下内容：

| 名字 | 说明 |
|---|---|
| `PLUGIN_API_VERSION` | 插件接口版本，当前是 `1`。和应用对不上时插件不会加载 |
| `RULE_SYSTEM` | 适用的规则系统，如 `"coc"` |
| `NAME` / `VERSION` / `DESCRIPTION` | 名称、版本、一句话说明，会显示在规则书页 |
| `COMPONENTS` | 组件列表，写法同官方组件 |

组件必须满足以下约束：

- **必须是可选组件**，也就是要声明 `toggle`（一个布尔参数）。房主打开它才生效。
- `id` 不能以 `coc7.` 开头，这是官方保留的前缀。建议写成 `作者名.规则名`。
- 不能和已有的组件、服务、村规参数、数据表重名。重名的插件不会加载，原因会显示出来。

组件可以带上这几样东西：

- `config`：村规参数（`RuleConfig` 子类，字段用 `rule_field` 声明）。规则页会自动出现对应的表单，`describe()` 和 `labels()` 会分别出现在 KP 的提示词和玩家看到的规矩摘要里。
- `hooks`：钩子实现所在的模块，一般写 `__name__`。函数用 `@hookimpl` 标注，函数名就是钩子名。
- `data`：数据表（`DataTable`），可以被房主在编辑器里改。
- `conditions`：本组件会给参战方挂的战斗状态，写成 `{"状态键": "中文名"}`，例如 `{"dazed": "眩晕"}`。钩子里 `conditions_add` 用到的状态键都应在这里声明，战斗界面据此显示状态名（用一个通用图标）；状态键不能和已有组件声明的重名。
- `kp_tools`：给 KP 新增的工具（`KpTool`），见下文「给 KP 加工具」。
- `provides`：服务槽位，一般用不到。

### 可用的钩子

钩子声明在 `server/app/rules/hooks.py`。函数的参数可以只取下表里列出的一部分。

| 钩子 | 时机 | 返回 |
|---|---|---|
| `check_modifiers(character_data, skill_name, difficulty, options)` | 每次技能检定掷骰之前 | `[{"kind": "bonus"或"penalty", "n": 1, "reason": "…"}]`，理由会显示在骰子卡上 |
| `max_hp(attrs, options)` | 计算最大 HP 时 | 一个整数，排在官方算法前面生效 |
| `wound_resolved(result, damage, max_hp, defender_data, options)` | 一次伤害结算之后（战斗与叙事扣血都会触发） | `{"conditions_add": [...], "lines": [...]}`；玩家角色身上的状态会存到角色卡 |
| `combat_turn_start(participant, options)` | 战斗中某人的回合开始时 | `[{"damage": 1, "reason": "…"}]`，即持续伤害 |
| `first_aid_applied(target, options)` | 急救成功之后（战斗内与叙事中都会触发） | `{"conditions_remove": [...], "lines": [...]}` |
| `condition_notes(conditions, options)` | 每回合给 KP 组上下文时，角色卡上挂着持续状态 | `["…"]`：告诉 KP 这个状态怎么演、战斗外怎么结算 |

`options` 是本局生效的规则，你的组件参数可以直接从它上面读，例如 `options.wound_penalty_percent`。

## 给 KP 加工具

除了改规则的行为，插件还能给 KP 新增工具：比如掷一张自定的随机遭遇表、查一张本桌的物价表。参考同目录下的 [`random_encounter.py`](random_encounter.py)。

```python
from app.rules.kp_tools import KpTool, KpToolContext, KpToolResult

def roll_encounter(ctx: KpToolContext, args: dict) -> str:
    ...
    return "随机遭遇（1D6=3）：……"   # 这段文字会回给 KP

Component(
    id="me.random-encounter", ..., toggle="random_encounter",
    kp_tools=(KpTool(name="roll_encounter", description="……", parameters={...}, execute=roll_encounter),),
)
```

| 字段 | 说明 |
|---|---|
| `name` | 工具名，小写 snake_case（如 `roll_encounter`） |
| `description` | 给 KP 看的说明：什么时候用、什么时候别用。写得越具体，KP 越不会乱用 |
| `parameters` | 参数的 JSON Schema，`{"type": "object", "properties": {...}, "required": [...]}` |
| `execute(ctx, args)` | 执行函数，普通函数和 `async` 函数都行。`args` 是 KP 给的参数（dict） |

`execute` 能拿到的上下文 `ctx`：

| 字段 | 说明 |
|---|---|
| `ctx.db` | 本轮的数据库会话。写了库要自己 `ctx.db.commit()` |
| `ctx.session_id` / `ctx.game_session` | 本局 |
| `ctx.module` | 本局模组 |
| `ctx.player_char` / `ctx.teammates` | 本轮行动的角色与其队友 |
| `ctx.ruleset` | 本局生效规则。组件参数从 `ctx.ruleset.options` 上读 |

返回值：

- 一段文字（`str`）：回给 KP，KP 据此接着叙述。
- `KpToolResult(text, suspend=True)`：回给 KP 之后本轮生成就此收束，适合「要等玩家先做点什么」的工具。

规则：

- 工具跟着组件的开关走：房主没在规则页打开这个组件，KP 就看不到它的工具。
- 工具名**不能**和内置工具（`dice_check`、`hp_change`、`scene_change` 等）重名。内置工具带着检定、扣血这些确定性结算，不允许被覆盖。重名的插件不会加载，原因会显示在规则书页。两个插件的工具重名也一样。
- `execute` 抛了异常不会让这一轮跑团中断：KP 会收到「工具执行出错，请直接继续叙述」，异常写进日志。
- 工具只在工具调用模式（默认开启）下可用，旧的文本指令路径看不到插件工具。

## 已知限制

- 插件新增的战斗状态在战斗界面上显示组件声明的中文名，但统一用一个通用图标，不能自定义图标；没在 `conditions` 里声明的状态只显示状态键。
- 战斗外没有「回合」，`combat_turn_start` 只在战斗里触发。持续状态在战斗外的结算交给 KP，靠 `condition_notes` 告诉它。
- 角色卡上的持续状态在战斗界面和角色卡面板上都有徽标，名称和图标的规则同上：显示 `conditions` 里声明的中文名，图标统一为通用图标。
