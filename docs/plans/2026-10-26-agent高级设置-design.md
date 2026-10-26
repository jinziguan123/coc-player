# Agent 高级设置：运行预算与上下文档位（设计稿）

日期：2026-10-26。状态：已按本稿实施（档位含事件预算上限，含规划器超时与工具循环步数，三档数值同下表）。

## 目标与范围

在设置页新增「高级」页，让本机用户调整两类 agent 参数：

1. **运行预算**：已经参数化、目前只能改 `.env` 的运行设置，加上两个直接对应「慢模型超时」「KP 绕圈」的限额。
2. **上下文档位**：省钱 / 均衡 / 完整，一档同时决定若干联动的上下文常量。

全局生效（跟本机的模型与花费走，不是房间村规），存在本机配置文件，每项有默认值、取值范围和「恢复默认」。
不开放原始提示词预算常量（`MAX_SYSTEM_TOKENS`、`RESERVE_FOR_OUTPUT/TOOLS` 等）：它们与静态手册长度和
「给下游注入留 6000」的测试护栏联动，单独改动的后果用户无从判断，改坏了只会让叙事悄悄变差。

## 一、运行预算参数

| 键 | 含义 | 默认 | 范围 | 当前读取点 |
|---|---|---|---|---|
| `generation_max_calls` | 每次生成最多模型调用次数 | 48 | 8–200 | `generation_manager.py:58` |
| `generation_max_tokens` | 每次生成最多 token | 500,000 | 50,000–3,000,000 | `generation_manager.py:59` |
| `generation_max_seconds` | 每次生成最长时长（秒） | 600 | 60–1800 | `generation_manager.py:60` |
| `housekeeping_max_seconds` | 后台收尾（摘要、幕后推演）最长时长（秒） | 90 | 30–600 | `generation_lifecycle.py:150` |
| `narration_buffer_high_risk` | 高风险回合整段缓冲到终检后再放出 | 开 | 开/关 | `turn_orchestrator.py:409` |
| `narration_buffer_timeout_seconds` | 缓冲等待终检的超时（秒） | 60 | 10–300 | `turn_orchestrator.py:414` |
| `narration_buffer_on_failure` | 终检超时或失败时：放行 / 扣下 | 放行 | release / withhold | `turn_orchestrator.py:415` |
| `checks_use_fast_model` | 终检、去口癖先用快模型 | 开 | 开/关 | `llm_factory.py:90` |
| `planner_timeout_seconds`（新增） | 规划器墙钟超时（秒） | 120 | 30–600 | `turn_planner.py:1087`（现为模块常量） |
| `kp_tool_loop_max_steps`（新增） | KP 工具循环每段最多轮数 | 20 | 4–40 | `kp_tool_loop.py:175`（现作为函数默认参数，定义时求值） |

不开放：`kp_volatile_tail`（提示缓存布局的技术开关，用户无从判断）；后台收尾内部的调用次数与 token 上限（12 次 / 10 万，属实现细节）。

## 二、上下文档位

**需要先确认的一点**：最近原文保底（`MIN_RECENT_EVENTS`）只是**下限**——事件区会一直按预算往前填满。
大窗口模型下只调保底、摘要和场景窗口，「省钱」几乎不省 token。因此建议档位同时控制**事件预算上限**。

| 档位 | 事件预算上限 | 最近原文保底 | 兜底摘要预算 | 最近去过场景保留数 |
|---|---|---|---|---|
| 省钱 | 64,000（估算 token） | 6 条 | 1,200 | 3 |
| **均衡（默认，等于现状）** | 150,000 | 10 条 | 2,000 | 6 |
| 完整 | 200,000 | 16 条 | 3,000 | 10 |

- 事件预算上限对应 `CONTEXT_BUDGET_CEIL`；按窗口取比例（0.6）与下限 48,000 不变，所以小窗口模型三档的预算相同，
  只有保底、摘要、场景窗口有差别。
- 保底按现有语义**不受预算约束**；「完整」档在小窗口模型上可能更容易顶到窗口，设置页给出提示。
- 有未了结线索的场景仍一律保留（`_visible_scenes` 的现有规则），场景窗口只影响其余已去过的场景。
- 读取点：`context.py` 的 `resolve_context_budget`、`MIN_RECENT_EVENTS`（:1675 起）、`MAX_SUMMARY_TOKENS`、
  `VISITED_SCENE_WINDOW`（:543）。

## 三、存储与生效

- 文件：与数据库同目录的 `agent_settings.json`（与 `ai_settings.json` 同一规则：开发时在 `server/`，打包时在 app-data）。
- 只存用户改过的项：`{"version": 1, "context_preset": "balanced", "overrides": {"generation_max_calls": 64}}`。
  未知键原样保留（向前兼容），写入用临时文件 + 原子替换。
- 优先级：配置文件覆盖 > 环境变量 / `.env` > 代码默认。界面上的「默认值」显示后两者合成的值。
- 恢复默认：删除对应覆盖项；「全部恢复默认」清空覆盖并把档位设回均衡。
- 文件损坏或某项越界：该项回落默认并记日志，不影响启动。
- 生效时机：每次生成开始时读取，下一次生成即生效，无需重启；进行中的生成不受影响。读取按文件修改时间缓存。

## 四、后端结构

- 新模块 `app/agent_settings.py`（与 `config.py` 同层，只依赖标准库、pydantic 和 `config`）：
  参数定义（默认、范围、说明）、档位表、读写、`current()` 返回合成后的只读值。
- 九处 `settings.xxx` 读取改为 `agent_settings.current().xxx`；`PLANNER_TIMEOUT_SECONDS`、`MAX_TOOL_LOOP_STEPS`
  与三个上下文常量保留为默认值，运行时读覆盖值（保持现有测试对模块常量的 monkeypatch 有效）。
- 接口（仅本机，`require_local_client`）：
  `GET /api/settings/agent` 返回各项的当前值、默认值、范围、是否被覆盖、档位；
  `PUT` 提交部分修改，越界返回 422 与可读原因；`DELETE`（可带 `key`）恢复单项或全部默认。
- 重新导出 OpenAPI 类型（`pnpm api:spec && pnpm api:types`）。

## 五、前端

- 设置页二级导航新增「高级」（`GiGears` 图标），新面板 `AgentAdvancedPanel`：
  - 上下文档位：三张选择卡（与「局内显示」同款），每张写明省什么、代价是什么。
  - 运行预算：数值输入带单位与范围、开关、下拉；每项说明一句影响，改过的项标「已修改」并有「恢复默认」。
  - 底部「全部恢复默认」（二次确认）。保存失败显示后端给的原因。
- 遵守项目图标规则（矢量图标，无 emoji）。

## 六、测试

- 后端：读写与原子写入、优先级（文件 > 环境 > 默认）、越界与损坏文件回落、未知键保留、单项/全部恢复；
  接口仅本机可用；`generation_manager`、规划器超时、工具循环步数、档位对预算/保底/摘要/场景窗口的影响各一条行为测试。
- 前端：面板渲染、修改保存、恢复默认、错误提示。
- 验证：后端全量 pytest + ruff；前端 `pnpm --filter web typecheck && pnpm --filter web build`。

## 七、实施记录

- 后端：`app/agent_settings.py`（参数定义、读写、优先级、校验）、`app/api/agent_settings.py`（GET/PUT/DELETE `/api/settings/agent`，仅本机）；
  九处读取点改为 `agent_settings.current()`；`turn_planner.planner_timeout_seconds()`、`kp_tool_loop.tool_loop_max_steps()` 显式覆盖优先、否则用模块常量；
  `app/ai/context.py` 新增 `context_preset_values` 与 `CONTEXT_PRESETS`，均衡档实时读原常量。
  `llm_factory.settings` 保留为兼容导出（测试经它打桩）。测试夹具把设置文件隔离到临时目录；`.gitignore` 忽略 `agent_settings.json`。
- 前端：设置页「高级」页 `AgentAdvancedPanel`（档位三卡、运行预算各项、单项与全部恢复默认、错误提示）。
- 测试：`tests/test_agent_settings.py` 26 例（存储、优先级、校验、损坏文件、未知键、原子写入、各读取点、档位对预算/保底/场景窗口的影响、接口与本机限制）；
  `AgentAdvancedPanel.test.tsx` 4 例。另在隔离的后端与数据库副本上做了界面检查（档位切换、数值保存、越界提示、单项与全部恢复）。
- 偏离：无。校验错误文案为「X 须在 8–200 次之间」（中西文间留空格、千分位）。
