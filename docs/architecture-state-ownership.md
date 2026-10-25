# 状态所有权与事务边界（当前实现）

更新日期：2026-10-25。本文描述当前代码；历史过程见 [后续验收记录](architecture-followup-2026-10-25.md)
和 [历史审计](history/architecture-audit-2026-10-25-before-followup.md)。

## 状态所有者

| 状态 | 存放 | 创建与修改 | 消费与清理 |
|---|---|---|---|
| 回合移动裁定 | `turn_state.turn_moves` | movement 经应用层裁定、装配和提交；movement_state 解释存档结构 | movement 查询接口；新回合 movement.begin_turn 清理 |
| 待检定移动 | `turn_state.pending_moves` | movement 经裁定与检定结算应用用例 | 逐人/共同障碍结算；movement.begin_turn 作废 |
| 自动检定标记 | `turn_state.auto_checks` | auto_check_state.record，同一事件与技能不重复追加 | auto_outcome 只读仍存活的骰子事件；clear 作废 |
| 待检定物品 | `turn_state.pending_item_gains` | planned_effects 根据规划暂存 | reward_settlement 消费或作废 |
| 物品兑现台账 | `turn_state.item_delta_keys` | reward_settlement 与库存、事件同写 | 按回合锚点、物品、角色和增减方向去重 |
| 待检定线索 | `world_state.pending_clue_reveals` | world_memory 纯变换，由 turn_context 等调用方赋值 | reward_settlement 兑现；world_memory.discard_pending_clue_reveals 作废 |
| 线索台账 | `world_state.clue_ledger` | world_memory 纯变换，由收益结算或其他确定性揭示入口持久化 | 上下文、结局与复盘读取；不会因新回合清空 |
| 回合确认 | `turn_state.turn_confirm` | turn_state_service | commit_turn 消费；确认模块不清除其他所有者的状态 |
| 角色位置 | `SessionNavigation.party_locations`、房主锚点 `GameSession.current_scene_id` | navigation_service 持有底层写入；由下表入口驱动 | 无显式记录时回落到房主锚点；位置不随回合清理或重生成撤销 |

check_gating.begin_turn 只编排各所有者的清理函数，一次提交；任一步失败整体回滚。
只有移动状态发生清理也返回 True；无变化不提交。空自动检定/线索残留键同样会清除。

## 位置写入入口

| 入口 | 确定性写入边界 | 提交后的行为 |
|---|---|---|
| movement 的批次执行 | 同批移动、留守位置固定、移动事件、裁定与待检定记录更新同事务 | 广播、场景配图；配图失败不撤销移动 |
| turn_travel.commit_pending_travel | 在动作转正后、KP 上下文构建前调用 movement.travel；每次前往走移动事务 | 原动作事件由前序阶段保存，不声称整批暂存动作与所有前往同事务 |
| 人工 KP `_exec_move_actor` | 外层事务调用 movement.stage_characters，只暂存位置/留守者；目的地公开、人工操作事件一起提交 | 提交后生成返回消息；不做普通前往的通行校验 |
| AI 队友 travel/stay | 位置更新或留守固定与 action 事件同事务 | 提交后输出行动与私有记忆钩子；记忆不纳入位置事务 |

房主单独移动时，先固定同处、未显式记录且不同行的队友，防止他们随锚点回落而被带走。
人工 KP 挪人只移动选中角色；自然语言与地图前往按各自裁定确定跟随者。

## 收益结算入口

`reward_settlement` 是收益兑现的单一实现，不依赖规划器、回合编排或 session_service。
它使用库存接口、event_store、auto_check_state、world_memory 和 effect_channel。

- `settle_check_rewards`：待物品消费、整批库存、事件、去重台账、线索候选消费与线索台账一起提交。
- `settle_auto_rewards`：在上述事务中追加自动检定标记，仅兑现技能匹配的物品/线索。
- `settle_pending_item_gains` / `settle_pending_clue_reveals`：保留单类结算的兼容入口，各自拥有事务。
- `apply_item_change`：即时物品效果仍按单件短事务；批量待收益结算使用 `commit=False`，由外层统一提交。
- 线索首次发现的配图由调用方注入，在提交后执行。失败记日志并清理配图未提交事务，不撤销收益。

effect_channel 的 `commit=False` 是“调用方拥有事务”的暂存模式，不是另开嵌套事务。
调用方必须统一提交/回滚；不能把仍含内部 commit 的函数包在外层事务里声称原子性。

## 重生成、失败与重试

| 情况 | 行为与保证 |
|---|---|
| 同回合重新生成 | 复用移动裁定，不重复移动；已兑现物品按台账去重，不随叙事撤销 |
| 移动批次失败 | 位置、事件、裁定与待检定记录一起回滚，原提案可重试 |
| 收益结算失败 | 整批收益回滚，待兑现条目保留；显式重试收益入口不重复发放 |
| 人工 KP 挪人失败 | 位置、留守者、目的地公开及事件一起回滚 |
| 新回合开始 | 按所有者作废上一轮未完成的移动、物品、线索、自动检定暂存；位置保留 |
| 提交后配图失败 | 不重做业务写入；尚无可靠补发队列 |

骰子事件、逐人移动与收益仍是顺序执行的不同事务；没有跨整个回合的原子提交。
没有后台恢复队列，进程崩溃后不会自动续跑未完成结算；重新投骰也不等价于重试原收益。
人工 KP 已成功请求的重复发送没有请求级幂等键。不能将数据库回滚测试视为分布式“恰好一次”保证。

## 查询与兼容边界

- party_queries 持有席位和队员只读查询；session_service 保留原名导出。席位顺序、空席与缺失角色跳过、排除角色语义不变。
- planned_effects 保留收益函数导出；新编排直接调用 reward_settlement。
- movement.turn_records、PENDING_KEY、MOVES_KEY 保留兼容，但其他应用模块不解释移动存档键。
- check_gating 保留 auto_outcome、skill_matches 导出；turn_context 保留旁白标记转换兼容导出。
- 暂存前往的位置写入已迁出 turn_context，但该模块仍承担世界记忆等职责，尚非纯只读上下文模块。
- 存档键、数据库结构与模组格式不变，无需迁移或重新导入模组。

## 验证与架构限制

测试覆盖陵墓独行、留守、门控成败、分队重聚、重生成、取消、失败回滚及重试，并检查实际生成时的 KP 输入上下文。
收益测试在第二件物品、线索更新与最终提交处注入异常；人工 KP 测试在操作事件和提交处注入异常。
结构测试防止移动状态越界、收益结算回调上层编排、规则配置反向依赖会话服务。

测试使用确定性模型替身，没有验证真实 LLM 的理解率或叙述质量。当前静态依赖图仍有 22 模块强连通分量，
不把局部职责提取表述为全项目已完成六边形架构或 DDD。最新方法与范围见 [架构审计](architecture-audit-2026-10-25.md)。
