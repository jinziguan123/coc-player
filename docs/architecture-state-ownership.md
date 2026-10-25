# 移动、检定、回合与位置：状态所有权与事务边界

日期：2026-10-25。依据是实际调用与写入（文件与函数见各表），区分**当前实现**与**目标设计**。
这里只划分实际存在的状态和写入方，不把模块命名成聚合或限界上下文。

## 一、状态清单与所有者（当前实现）

| 状态 | 存放 | 创建 | 修改 | 消费 | 清理 |
|---|---|---|---|---|---|
| 回合移动裁定 | `turn_state.turn_moves` | `movement.adjudicate_plan`（经 `prepare_movement_batch`） | `movement.settle_pending_moves`（个人状态与整条状态） | `tool_guard`、`busy_ids`、`has_move_evidence`、`has_verdict_for`、重生成时的说明恢复 | `movement.begin_turn`（由 `check_gating.begin_turn` 调用） |
| 待检定移动 | `turn_state.pending_moves` | `adjudicate_plan`（新条目替换参与者重叠的旧条目） | `settle_pending_moves` | `pending_unissued`（补挂检定）、`has_pending_moves` | 结算完自动移出；`movement.begin_turn` |
| 自动检定结果 | `turn_state.auto_checks` | `check_gating.record_auto_check` | — | `check_gating.auto_outcome`（规划物品是否直接给） | `check_gating.begin_turn` |
| 待检定物品收益 | `turn_state.pending_item_gains` | `planned_effects._ensure_planned_items` | — | `planned_effects.settle_pending_item_gains`（手动投骰、自动检定、人类 KP 台面） | 结算即清；`check_gating.begin_turn`；`_discard_pending_item_gains` |
| 待检定线索 | `world_state.pending_clue_reveals` | `world_memory` 暂存（经 `turn_context`） | — | `planned_effects.settle_pending_clue_reveals` | 结算即清；`check_gating.begin_turn` |
| 回合确认 | `turn_state.turn_confirm` | `turn_state_service.set_turn_confirm` | 同上 | `turn_confirm_state` | `turn_state_service.commit_turn` |
| 角色位置 | `SessionNavigation.party_locations`、`GameSession.current_scene_id`（房主） | `navigation_service.set_char_location` | 见下表 | `get_char_location`（无记录回落到房主所在） | 无（位置不随回合撤销） |

### 位置写入方

| 写入方 | 场景 | 事务 | 是否固定留守者 |
|---|---|---|---|
| `movement._stage_move`（经 `commit_movement_batch`） | 自然语言移动、检定通过、KP 工具 `scene_change`、大地图前往、人类 KP 强制挪人 | 位置、留守固定、移动事件、裁定与待检定记录一笔提交 | 是：房主移动时先写实同处无记录的人 |
| `team_turn_service` 队友前往/留守 | AI 队友自主决定 | 位置与行动事件分两次提交 | 不涉及（AI 队友不是房主） |

## 二、谁提出、谁裁定、谁写入

- **提出**：规划器 `scene_policy.moves`、KP 工具 `scene_change`、大地图前往、检定结果。
- **裁定**：`application/movement_adjudication`（证据、出口、通行、检定需求、跟随）；
  检定落地由 `application/movement_settlement` 结算，规则来自 `domain/movement.settle_check`。
- **写入**：只经 `commit_movement_batch` 与服务层的 `_MovementWriter`，同一事务。
  其他模块需要移动状态时只调用 movement 的查询函数（`tool_guard`、`busy_ids`、`has_pending_moves`、
  `has_verdict_for`、`has_move_evidence`），不解释存档键。

## 三、必须同事务的操作

1. 一个移动批次内的全部落位、留守固定、移动事件、裁定记录与待检定条目的增删（已满足，数据库测试覆盖回滚与重试）。
2. 一次检定结算的条目消费、落位与记录更新（已满足）。
3. 回合开始时作废各类门控暂存：当前由 `check_gating.begin_turn` 一次提交（移动部分 `movement.begin_turn` 只改不提交）。

不要求同事务：提交后的广播与配图（配图失败不回滚移动）；AI 队友自主前往的事件与位置（当前分两次提交，见目标设计）。

## 四、重生成、重试、回合切换

| 时机 | 移动裁定 / 待检定移动 | 物品 / 线索暂存 | 位置 |
|---|---|---|---|
| 重新生成（同一回合锚点） | 复用裁定记录，不重复落位或写事件 | 已兑现的用 `item_delta_keys` 去重 | 不回滚 |
| 写入失败重试 | 整批回滚，重试时重新裁定，只执行一次 | 各自提交 | 随批次回滚 |
| 新的玩家回合 | `movement.begin_turn` 作废 | `check_gating.begin_turn` 作废 | 保留 |
| 真人回合确认 | 保留（修复前被误删，见「已知问题 1」） | 保留 | 保留 |

## 五、本轮已收敛的越界

1. **移动模块回调计划效果**：`movement.pending_unissued` 曾用函数内导入回调
   `planned_effects.requested_check_settled`，形成 movement ↔ planned_effects 互相导入。
   现在由调用方 `planned_effects._ensure_planned_scene` 注入“是否已挂出检定”的判断，
   按人/共同障碍的补挂规则移入应用层 `checks_to_issue`（纯函数）。
2. **其他模块直接解释移动存档键**：`turn_effects` 读 `movement.PENDING_KEY`、
   `turn_orchestrator` 遍历 `turn_records` 比较 `target`。改为 movement 的查询函数
   `has_pending_moves`、`has_verdict_for`。
3. **可执行约束**：结构测试禁止 movement 依赖 planned_effects 与 check_gating；
   除 `services/movement.py` 与 `application/movement_state.py` 外，任何模块不得出现
   `turn_moves`/`pending_moves` 字面量或访问 `movement.PENDING_KEY`、`MOVES_KEY`、`turn_records`。

## 六、已知问题（单独记录，不混入保持行为的重构）

1. **真人回合确认会抹掉移动与自动检定状态**（已复现并单独修复）：
   `turn_state_service._write_confirm_map` 为了清理旧迁移铺在顶层的确认键，删除了所有不在
   `_EPHEMERAL_TURN_STATE_KEYS` 白名单里的顶层键，白名单没有 `turn_moves`、`pending_moves`、`auto_checks`。
   复现：暂存攀爬移动后另一名真人确认回合，`turn_state` 只剩 `turn_confirm`，之后攀爬成功也不移动。
   根因是回合确认模块用白名单替其他模块决定哪些键能活下来。
   修复：`_write_confirm_map` 只清理 `_confirm_map` 会当成旧确认读取的顶层值为 True 的键，读写同一规则；
   `test_turn_confirm_state_keys.py` 与 `test_natural_movement.py` 的回归用例在旧代码上失败、修复后通过。
2. **KP 强制挪动房主时会带走无位置记录的队友**（已修复）：`human_kp_actions` 曾直接调用 `set_char_location`，
   房主的位置即 `current_scene_id`，同处、没有显式位置记录的队友会随之“被移动”。
   按确认的预期“只挪选中的那一个角色”，改为经 `movement.move_characters` 落位：同一事务里先固定同处队友，
   不做通行校验。位置与留守固定一笔提交；公开目的地与系统事件仍在其后各自提交。
   `test_human_kp_flow.py` 的回归用例在旧代码上失败、修复后通过。
3. `check_gating.begin_turn` 只清理了移动状态时返回 False（返回值目前没有调用方使用，无行为影响）。

## 七、目标设计（尚未实施）

- 回合开始作废：各状态所有者各自提供“作废本回合暂存”的函数，`begin_turn` 只做编排与提交；
  目前移动已如此，物品、线索、自动检定仍由 `check_gating` 直接改键。
- 位置写入：会改变房主位置的入口都经过固定留守者的同一实现（问题 2 修复后已成立；AI 队友前往不涉及房主）。
- AI 队友自主前往的位置与事件合并为一次提交。

## 八、仍保留的兼容入口与迁移限制

- `movement.turn_records`、`PENDING_KEY`、`MOVES_KEY` 保留给测试与旧调用方；应用代码中移动模块之外已不再使用
  （结构测试约束）。`char_status`、`busy_ids` 仍是对外查询函数。
- `check_gating.skill_matches`、`turn_context._remap_marks_after_rewrite/_snap_offset` 为兼容导出。
- 存档键与状态字符串不变，无需迁移或重新导入模组。
- movement 与 turn_context 仍互相导入（`commit_pending_travel` 调 `movement.travel`），
  强连通分量仍为 28 个模块；本轮没有处理。

## 后续优化：回合清理与自动检定状态（2026-10-25）

`auto_check_state` 现在负责自动检定记录的追加、按存活骰子读取和作废；`check_gating` 保留兼容查询导出，
但只负责生命周期编排。`planned_effects` 直接读取状态接口，不再反向依赖 `check_gating`；
自动收益的技能匹配与兑现由 `planned_effects.settle_auto_rewards` 负责，原有先记录检定再兑现的事务顺序不变。
这消除了二者直接互相导入，不宣称整个间接依赖环已清除。

新回合清理分别调用移动、物品、自动检定与世界线索的清理函数：物品清理支持 `commit=False`，
线索清理返回世界状态副本，最终由门控入口统一提交。任何清理或提交失败都会 rollback。
仅移动记录被清理时现在也返回 True；没有改动不提交。自动检定/线索的空残留键也会被清除并报告变更。
回归测试验证只提交一次、无变化不提交及中途异常后全部状态恢复。

## 后续优化：位置执行入口与 AI 队友事务（2026-10-25）

暂存前往的执行迁至 `services/turn_travel.py`。AI 回合编排和人工 KP API 直接调用它，
调用位置仍在玩家动作转正之后、后续上下文构建之前。`chat_service` 经回合编排保留兼容入口；
`turn_context` 不再依赖 movement，也不再承担这项位置写入。上下文模块还有世界记忆等既有职责，
此处仅收拢位置执行，不宣称整个模块已经纯只读。

AI 队友自主前往和留守现在用现有 `effect_channel.transaction`：位置固定/更新与 action 事件都只 flush，
成功后统一提交，再刷新并输出事件。事件暂存后抛错会同时回滚位置和事件，重试只写一条行动事件。
私有记忆钩子仍在行动提交后执行，不纳入此事务，也不因本次调整重复生成决策。
`test_ai_teammates.py` 对 travel/stay 两条入口覆盖真实数据库的失败回滚及重试；分层测试防止上下文重新调用位置写入。

以上更新替代前文“位置与行动事件分两次提交”和对应目标项；原导航、通行规则、存档格式没有变化。
