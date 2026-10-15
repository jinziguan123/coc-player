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
- `provides`：服务槽位，一般用不到。

### 可用的钩子

钩子声明在 `server/app/rules/hooks.py`。函数的参数可以只取下表里列出的一部分。

| 钩子 | 时机 | 返回 |
|---|---|---|
| `check_modifiers(character_data, skill_name, difficulty, options)` | 每次技能检定掷骰之前 | `[{"kind": "bonus"或"penalty", "n": 1, "reason": "…"}]`，理由会显示在骰子卡上 |
| `max_hp(attrs, options)` | 计算最大 HP 时 | 一个整数，排在官方算法前面生效 |
| `wound_resolved(result, damage, max_hp, defender_data, options)` | 一次伤害结算之后 | `{"conditions_add": [...], "lines": [...]}` |
| `combat_turn_start(participant, options)` | 战斗中某人的回合开始时 | `[{"damage": 1, "reason": "…"}]`，即持续伤害 |
| `first_aid_applied(target, options)` | 急救成功之后 | `{"conditions_remove": [...], "lines": [...]}` |

`options` 是本局生效的规则，你的组件参数可以直接从它上面读，例如 `options.wound_penalty_percent`。

## 已知限制

- 插件新增的战斗状态在战斗界面上显示组件声明的中文名，但统一用一个通用图标，不能自定义图标；没在 `conditions` 里声明的状态只显示状态键。
- 叙事中的扣血（KP 写的 HP_CHANGE）不经过 `wound_resolved`，只有战斗轮里的伤害结算会经过它。
