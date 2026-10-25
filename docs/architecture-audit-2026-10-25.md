# 后端架构复查与分阶段进度

日期：2026-10-25。目标是降低实际依赖和状态写入耦合，而非追求目录形式。

## 复查方法与基线

当前没有可用的 codebase-memory-mcp 图工具，改用 Python AST 检查 `server/app` 的模块导入，
包含函数内导入，对 `from app.services import x` 按具体模块解析。排除 `__init__.py`，
未展开动态导入、兼容导出的符号归属和运行时调用；以下是静态模块依赖，不代表必然发生循环初始化错误。

本轮修改前共 203 个非包初始化模块，其中 services 89 个。
发现一个 28 模块强连通分量，包含 AI 规划、上下文、移动、检定、会话、导航和战斗相关模块。
直接互相导入的六对为：

- check_gating 与 movement；check_gating 与 planned_effects。
- movement 与 planned_effects；movement 与 turn_context。
- rule_options_service 与 session_service。
- style_editor 与 turn_context。

复核（接手会话用独立脚本重跑 AST 扫描）：204 个非包初始化模块，services 89 个；强连通分量仍为 28 个模块。
修改后 style_editor 与 turn_context 已不再互相导入，但 style_editor 仍在分量内（还经其他模块间接成环）。
其余五对互相导入都靠函数内延迟导入维持，具体调用链如下：

| 互相导入 | 正向（模块顶层） | 反向（函数内延迟导入） | 性质 |
|---|---|---|---|
| movement ↔ check_gating | `movement.settle_pending_moves` 调 `check_gating.skill_matches` 判技能是否对得上 | `check_gating.begin_turn` 调 `movement.begin_turn` 作废待检定移动 | 真实运行依赖；技能匹配是纯规则，回合清理是生命周期写入 |
| movement ↔ planned_effects | `planned_effects` 调 `movement.adjudicate_plan`、`pending_unissued`、`has_move_evidence` | `movement.pending_unissued` 回调 `planned_effects.requested_check_settled` | 回调式循环：调用方把自己的查询绕回给被调用方 |
| movement ↔ turn_context | `movement` 调 `turn_context.guard_turn_events` 读本轮原话、`_scene_name` 取场景名 | `turn_context.commit_pending_travel` 调 `movement.travel` 落下大地图前往 | 上下文构建模块里藏着一次位置写入；`_scene_name` 是私有名被跨模块使用 |
| check_gating ↔ planned_effects | `planned_effects` 调 `check_gating.auto_outcome` | `check_gating` 结算物品/线索时调 `planned_effects.settle_pending_*` | 检定结算与计划效果互相驱动 |
| rule_options_service ↔ session_service | `session_service` 设置村规时规范化规则选项 | `rule_options_service` 生成摘要时读会话成员 | 双方都是延迟导入 |

前三对都经过移动模块，与第二、三阶段直接相关：技能匹配与 `requested_check_settled` 回调都在检定结算路径上，
`begin_turn` 是回合生命周期的跨模块写入。这些放到对应阶段一起处理，避免第一阶段先拆一半。

模块依赖数较高的是 turn_orchestrator（52）、kp_tool_loop（31），以及 planned_effects、movement、turn_effects（各 21）。
这些数字只帮助定位入口，不作为必须拆分的依据。

## 风险及处理顺序

| 问题 | 证据与影响 | 本次选择 |
|---|---|---|
| 回合与检定生命周期跨模块 | check_gating.begin_turn 调 movement.begin_turn；自动检定又调用 planned_effects；移动读取技能匹配 | 收益高、事务风险较高，第二阶段先分离移动结算批次，再在第三阶段明确写入所有权 |
| 文字润色反向依赖回合上下文 | style_editor.polish_result 仅为重映射位置导入 turn_context；后者又调用润色器 | 第一阶段处理：纯位置变换独立，低风险且可验证切断直接反向边 |
| 会话设置与规则摘要相互依赖 | session_service 设置村规依赖规则规范化；rule_options_service 为状态摘要读取会话成员 | 记录为后续候选，避免本次同时拆成员查询与规则配置 |
| 高扇出生成编排 | turn_orchestrator 连接生成、工具、持久化和状态服务 | 不机械拆分，需按具体生命周期分析事务与副作用后再决策 |

## 第一阶段：完成的边界调整

旁白改写后的对话、分组与工具事件位置变换迁入 `application/narration_marks.py`。
润色器直接调用该模块，turn_context 保留原函数名的兼容导出；算法与输入结构不变。
这消除了 style_editor → turn_context 的直接依赖，**不宣称消除了整个强连通分量**。
现有润色测试验证真实接线，独立测试验证空原文、句界、事件顺序与偏移边界，结构测试防止反向依赖重新出现。

## 第二阶段：检定结算批次协调

技能匹配、待执行记录、位置资格、个人状态、裁定记录更新与事务提交的路径为：
手动投骰 `turn_orchestrator._settle_rolled_check` / 自动掷 `turn_effects` → `movement.settle_pending_moves`
→（原）逐条 `check_gating.skill_matches` + 领域 `settle_check` + 记录更新 → `commit_movement_batch`。

现在中间的协调是应用层纯函数 `application/movement_settlement.settle_pending_checks`，
输入为待检定与裁定记录快照、技能、结果和位置快照，输出移动命令和状态更新；数据库刷新、位置读取、
提交、日志和展示留在服务适配边界。技能匹配迁入 `domain/checks.py`，消除 movement ↔ check_gating 互相导入。
互相导入从五对降为四对；强连通分量仍为 28 个模块（movement 还经 planned_effects、turn_context 等成环）。
详见 `docs/architecture-movement.md` 第十二轮。

## 第三阶段：状态所有权与事务边界

结论与证据见 `docs/architecture-state-ownership.md`。本阶段代码收敛：
movement 不再回调 planned_effects（补挂判断由调用方注入，规则移入 `checks_to_issue`）；
其他模块不再解释移动存档键；以结构测试约束。互相导入从四对降为三对
（剩余：check_gating ↔ planned_effects、movement ↔ turn_context、rule_options_service ↔ session_service）。
调查中发现回合确认会抹掉移动与自动检定状态，按“业务漏洞单独修复”的要求另行提交。

## 持续进度

当前阶段：三个阶段均已完成，停止扩张。

| 提交 | 内容 | 验证（server 下全量） |
|---|---|---|
| `7d1a9d1` | 第一阶段：审计；旁白位置变换独立，切断 style_editor → turn_context | 2891 通过、11 跳过；Ruff 通过 |
| `c11a035` | 第二阶段：检定结算批次协调进应用层；技能匹配下沉领域 | 2908 通过、11 跳过；Ruff 通过 |
| `ddeb266` | 第三阶段：状态所有权说明；切断 movement → planned_effects 回调；收拢移动存档键解释 | 2914 通过、11 跳过；Ruff 通过 |
| `45e62f6` | 单独修复：真人确认回合误删移动与自动检定状态 | 2916 通过、11 跳过；Ruff 通过 |
| 见 Git 日志 | 单独修复：人类 KP 强制挪人只挪选中角色 | 2918 通过、11 跳过；Ruff 通过 |

每次提交前均通过 `git diff --check`。未推送远端。已知无关文件（2026-10-22 计划草稿、tmp 下截图与脚本）未纳入提交。

## 最终架构评估

已改善的部分：
- 移动的规则、编排与存档转换分层清楚：领域（规则）→ 应用（裁定、结算、补挂、批次装配、提交端口、类型化状态）→
  服务（ORM 读取、对象映射、事务适配与展示）。裁定、检定结算和补挂规则都能不依赖数据库测试。
- 互相导入从六对降为三对；移动模块对外不再有反向依赖（只剩 turn_context 调 movement.travel 一处）。
- 移动状态的存档键只由移动模块解释，并有结构测试约束。

没有改变的部分：
- 静态强连通分量仍为 28 个模块。这次只拆了几条直接互相导入的边，services 整体仍高度耦合；
  turn_orchestrator（52 个依赖）、kp_tool_loop、planned_effects、turn_effects 仍是混合编排、持久化与 AI 调用的大模块。
- 这只是局部边界改善，不是全项目的六边形架构迁移。

## 剩余问题（按优先级）

1. ~~KP 强制挪动房主会带走无位置记录的队友~~：已按“只挪选中的那一个角色”修复。
2. **回合开始作废的所有权**：`check_gating.begin_turn` 直接改物品、线索、自动检定的键；
   宜让各所有者提供作废函数，`begin_turn` 只编排并一次提交。可顺带修正其返回值。
3. **check_gating ↔ planned_effects 互相导入**：自动检定兑现物品/线索要回调 planned_effects；
   可参照第三阶段，用调用方注入或把兑现函数移到所有者。
4. **movement ↔ turn_context**：上下文构建模块 `commit_pending_travel` 里有位置写入；
   宜把“落下大地图前往”移到回合编排处调用，turn_context 只读。
5. **AI 队友自主前往**位置与事件分两次提交；`rule_options_service ↔ session_service` 互相导入。收益较低，按需处理。

## 验收后续进度

- `4931f68`：固定教育增强测试的成功/失败输入，清理文件尾空行；全量 2919 通过、11 跳过，Ruff 通过。
- 已完成新回合状态清理所有权和自动检定状态读取边界；全量 2923 通过、11 跳过，Ruff 通过。提交见本文件日志。
- 暂存前往的写入入口及 AI 队友位置与事件事务已完成，最终验证见下文。

### 本轮后续执行结果

- `04726e6`：清理状态按所有者调用、统一提交与回滚；自动检定状态与收益编排分离。全量 2923 通过、11 跳过，Ruff 通过。
- 暂存前往的位置执行已迁出上下文模块；AI 队友前往/留守的位置与事件已同事务提交，相关测试 162 通过。
- 五项后续工作均已完成；最终全量 2926 通过、11 跳过，Ruff 与差异格式检查通过。最后一组提交见本文件日志。没有扩展到通用仓储、DI 或前端改造。
- AST 复查直接互相导入仅剩 `rule_options_service ↔ session_service`；间接依赖环仍可能存在，未将直接边减少等同于全图无环。

### 规则配置与会话查询解耦

- 将席位与队员只读查询提取到 `party_queries`；`session_service` 保留原名导出，规则上下文直接调用查询模块。
- 消除 `rule_options_service ↔ session_service` 直接互相导入，新增结构约束防止回退。查询顺序、空席/缺失角色处理、角色排除语义未改变。
- AST 扫描 `server/app` 的显式绝对导入（含局部导入、排除 `__init__.py` 和动态导入）未发现直接双向边；未据此断言不存在间接依赖环。
- 验证：相关测试 55 通过；全量 2927 通过、11 跳过，Ruff 与差异格式检查通过。
