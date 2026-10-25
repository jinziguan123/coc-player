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

## 持续进度

- 第一阶段：已完成。验证：后端全量 2891 通过、11 跳过；Ruff 通过；`git diff --check` 通过。提交号见 Git 日志（「第一阶段」提交）。
- 第二阶段：已完成。验证：后端全量 2908 通过、11 跳过；Ruff 通过；`git diff --check` 通过。提交号见 Git 日志（「第二阶段」提交）。
- 第三阶段：待整理移动、检定、回合与导航状态所有权，选择一个明确的越界写入或重复解释收敛。
- 未解决：上表其余四对互相导入；`check_gating.begin_turn` 只清理了移动状态时返回值仍为 False（待第三阶段核实影响）。
- 已知无关文件：既有 2026-10-22 计划草稿与 tmp 下审计/截图文件，不纳入提交。
- 下一条操作：第三阶段，追踪 turn_moves / pending_moves / auto_checks / pending_item_gains 等回合状态的创建、修改、消费与清理。
