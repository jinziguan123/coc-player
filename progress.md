Original prompt: 根据/Users/jinziguan/Desktop/trpg-player/docs/plans/2026-07-23-sandbox-enrich-terrain-plan.md文档进行开发

## 2026-07-23

- 已读取实施文档，按任务三、任务二、任务一的顺序开发；每项独立提交。
- 工作区原有未跟踪目录 `output/`，与本任务无关，不触碰。
- 当前会话未提供 codebase-memory-mcp 图谱工具，代码发现退回本地检索。
- 已实现空域地形纯函数与 Konva 最底层渲染：Voronoi 归属、距离衰减、未知区域降透明度、稀疏装饰和 2000 格保护。
- 已补 `terrain.test.ts`，待运行前端测试、类型检查、构建与浏览器视觉验收。
- 空域地形场已通过 4 项单测、tsc、Vite 构建、浏览器截图和拖拽重算验收，提交 `1651a6e`。
- 已新增十种共享地貌枚举，并为场景详情增加地貌查看 badge 与编辑下拉；待验证改地貌、保存、沙盘变色和未落位场景保存。
- 地貌选择器已通过前端 43 项测试、tsc、Vite 构建和浏览器验收：密林保存后影响整片地形；新增场景仅选水域后保存，后端自动补坐标且 biome 保留。临时验收模组已删除。
- 已实现 AI 沙盘补全后端服务与接口：输入最小化、深拷贝合并、枚举/连接/坐标确定性校验、失败零写入；已补 5 组单测，待运行。
- AI 补全后端 6 个用例已通过；前端已增加仅查看态可用的确认按钮、请求状态与成功后重载逻辑，并补组件测试，待运行前端检查。
- AI 补全已完成端到端验收：固定响应假模型下，确认弹窗、POST、重新加载、成功提示、编辑态隐藏均正常；地貌、连接和语义坐标已落库并正确渲染，浏览器无非预期控制台错误。
- AI 补全失败分支已通过浏览器拦截 400 验收，提示“AI 补全失败：模型返回坏 JSON”；验收模组、临时进程、临时配置和 Playwright 符号链接均已清理。
- 最终全量检查通过：后端 968 项测试与 Ruff；前端 44 项测试、tsc 和 Vite 生产构建。

## TODO

- [x] 实现并验证空域地形场。
- [x] 实现并验证地貌选择器。
- [x] 实现并验证 AI 一键补全。

## 2026-07-23 补充需求

- 用户要求沙盘编辑态直接修改单节点地貌，并支持多选后的批量修改。
- 用户要求场景节点之间保留普通地形格，地图边缘场景周围至少补齐一圈普通格。
- 已将场景映射到间隔为 2 的纯前端视觉网格，持久化坐标、连接与旅行语义保持不变；拖拽会映射回原坐标。
- 已增加节点点击多选、全选/取消全选、清空选择和选中集合地貌下拉；单选与批量修改共用同一流程，随模组保存落库。
- 已补场景间隔、边缘六邻格、单节点改地貌与批量保存测试；针对性 9 项测试及 TypeScript 检查通过。
- 浏览器验收通过：相邻逻辑场景之间至少隔一个普通地形格，边缘有完整一圈填充；单选地貌、全选批量地貌、保存回读和加宽网格后的拖拽吸附均正常，控制台无错误。
- 全量前端检查通过：47 项测试、TypeScript 检查和 Vite 生产构建；临时验收模组和 Playwright 符号链接已清理。

## 2026-08-15 评审整改

- 修复 `_party_distribution_section` 对 `navigation.party_locations = NULL` 的兼容，Evals smoke 回到 14/14。
- SPA 回退路由 `include_in_schema=False`，OpenAPI 导出不再依赖被 gitignore 的 `apps/web/dist`；重导 `openapi.json` 与 `generated.ts`，干净 checkout 导出零 diff。
- `session_service.py` 拆出 `event_store.py`（事件分页/检索/落库）、`turn_state_service.py`（回合确认/待投检定）、`navigation_service.py`（位置/连通图/地图可见性），门面保留 re-export。
- 统一 `turn_state.turn_confirm` 嵌套口径，兼容旧迁移顶层数据；`commit_turn` 不再误伤 pending 键。
- 前端路由级代码分割，首屏 JS 从约 1.7MB 降到约 361KB。
- 新增 [CoC 规则覆盖矩阵](docs/coc-rule-coverage.md) 与 [发布缺口门禁](docs/release-gates.md)，把非代码可完成项显式化。
- 验证：后端 1678 项测试通过 + Ruff；前端 289 项测试、tsc、Vite 构建通过；Rust 47 项测试通过。


## 2026-08-15 位置意图硬闸

- 玩家只提到/打算去 B 时，在 KP 生成前清洗 `plan.scene_policy.scene_change` 并注入「当前位置硬约束」。
- 扩充 `_NON_COMMITTAL_MOVE_MARKERS`：可能/之后/吧/看看吧 等措辞不再被误判为显式移动。
- TurnValidator 增加 `location_context` 与「位置越界」校验项，位置硬约束轮次强制终检并改写落库版本。

## 2026-08-15 路人归属、位置旁路与沙盘实时刷新

- 无名角色说话前缀改为右剥离式解析：老板娘玛莎/护工等不再被归到最近一个模组 NPC，气泡与头像不再张冠李戴。
- 检定申请/投骰续写等旁路也接入位置意图硬闸与定点终检，堵住「人不移动但旁白先到 B」的剩余入口。
- 前端收到旁白/对话/系统日志后节流重拉 locations，沙盘解锁新地点不再需要手动刷新。
- 验证：后端 1687 项测试通过、Ruff 通过、Evals smoke 14/14；前端 289 项测试、tsc、构建通过。
- 验证：后端 1685 项测试通过、Ruff 通过、Evals smoke 14/14。

## 2026-08-15 大厅选人舞台优化

- 真人玩家挑/换角色时，角色选择不再埋在席位卡底部：新增 `CharacterSelectStage` 选人舞台，置顶于大厅中段，标题、卡牌阵列、选中高亮与逐张浮入动画构成守望先锋/杀戮尖塔式选人界面。
- 右侧档案栏在选人模式下常驻：未选时给提示，点选后展示大纹章、职业年龄、HP/SAN、擅长技能与确认入座按钮，下方继续展开完整角色面板。
- 选人期间大厅聊天自动收成圆形浮标并贴在右栏与主栏之间，不遮挡卡牌阵列；席位卡中的旧角色选择区同步隐藏，避免重复出现。
- 验证：前端 289 项测试、tsc、Vite 生产构建通过；浏览器验收（含 8 张卡 mock）确认舞台置顶、底部开始栏常驻、聊天无遮挡、选中态与右侧档案正常。

## 2026-08-15 大厅候选池幽灵占用修复

- 定位「角色管理可见、大厅可用调查员卡不可见」：`claim_seat` 给主角席换人时没有同步 `game_sessions.player_character_id`，`active_character_ids` 又同时读取该快捷字段，旧角色被幽灵占用。
- `claim_seat`/`kick_seat` 在主角席变动后调用 `_reseat_primary` 同步；`active_character_ids` 改为有席位记录时只认席位、无席位记录才回落快捷字段，历史脏数据自动自愈。
- 已备份并修复当前库中两条 `player_character_id` 与主角席不一致的数据；陈守一已恢复出现在鬼屋大厅候选池。
- 验证：后端全量 1689 项测试通过、Ruff 通过；前端 tsc、Vite 构建通过。

## 2026-08-15 大厅细节完善

- 房间码按钮内部改为 inline-flex 包裹，房间码与复制 icon 固定同行，不再被全局 `.badge` 的 inline-block 覆盖。
- 选人舞台新增「快速创建」：点击时由大厅页 POST 空白草稿（固定落本机角色库，避免 StrictMode 下 effect 双跑），再复用 `CharacterEditModal` 编辑；取消时删除草稿，保存后刷新候选池并直接进入右侧预览。
- 移除 AI 席位行内的「生成 AI 队友」按钮与对应 `genSeat`/`adoptTeammate` 逻辑，AI 队友只通过席位下拉指派已有角色。
- 验证：前端 289 项测试、tsc、oxlint、Vite 生产构建通过；浏览器验收房间码同行、快速创建弹窗开合、AI 席无生成按钮、8 卡工具栏无横向溢出。

## 2026-08-17 首页新手介绍

- 首页入口卡下方新增 `HomeIntro` 三段介绍：什么是跑团（KP/调查员/骰子三要素）、基本规则（CoC 七版 d100 成功等级表 + HP/SAN + 奖惩骰）、如何游玩本项目（接模型/挑本子/备角色/开局四步，每步挂可点入口）。
- 成功等级阈值与「技能不到 50 时 96–100 皆大失败」的边界对齐后端 `resolve_skill_check` 判定顺序，表格右列给出技能 60 的实际骰点区间（01 / 02–12 / 13–30 / 31–60 / 61–99 / 100）。
- 排版按「一屏读完」收敛：三段并排成三栏（900px 以下退回竖排）、容器 4xl→5xl、标题与各段间距整体收一档。1440×850 与 1280×800 均不出滚动条；1024 宽窗口仍超出约 32px。
- 验证：前端 289 项测试、tsc、oxlint、Vite 生产构建通过；浏览器验收暗色与羊皮纸双主题、720/1024/1280/1440 四档宽度无横向溢出、无控制台错误。
## 2026-10-17 真人 KP 战斗观局台

- 真人 KP 的战斗战术台从「整段主台只有棋盘」改为对开双页：左先攻梯、中棋盘（沿用 CombatStage，隐藏内部先攻列 / 行动面板 / 战斗日志抽屉）、中缝本场轮次、右桌边账页。窄于 1280px 仍回退原战术台。
- 新增 `KpCombatLedger`：把玩家发言、场外、骰果、战斗机械结算与待投检定并入同一条时间轴；五档筛选、顶部待办区（代投 / 撤销 / 填入检定 / 忽略）、玩家发言行内「发检定 / 回复」、回到最新。
- 新增 `KpCombatRoster`：先攻序、当前 / 下一个、HP / SAN / 状态与敌方并成一列，点行可把右页账页定位到该角色。新增 `KpDeskRail`、`KpConsoleDock`、`useKpCombatActions`、`useKpDesk`、`useMinWidth` 把接线拆出主页面；GameSessionPage 2392 行（红线 2410）。
- 作战底条改为受控草稿，账页「回复」预填「（回 某人：）」并走场外；观局台隐藏底条重复的最近结算，保留整宽输入与结束战斗。
- 左缘行悬停保留「换战棋轮廓」，仍走 set_token 同步全桌；观局台隐藏棋盘内部行动面板与战斗日志抽屉。
- 验证：前端 typecheck、74 个测试文件 517 项、Vite 生产构建通过；真实真人 KP 会话浏览器验收 1600×1000 / 1440×900、无横向溢出、筛选与回复预填正常。

## 2026-10-17 真人 KP 开战 / 追逐配置修复与控件统一

- 开战表单新增「参战调查员」多选：不点选＝全队，点选后只把选中的角色拉入先攻序，其余席位留在场外。后端 `start_combat` 同步过滤 party 与 `human_ids`，并兼容旧的 `character:<id>` 引用。
- 追逐支持多名逃者与多名追兵：表单统一成同一套 MultiSelect（模组 NPC + 临场追兵输入），后端 state 同时保存 `quarries` / `pursuers`，广播与玩家追逐面板显示完整名单。距离轨仍只有一条，由最慢逃者与最快追兵代表全组判定（界面上明示）。
- 修复「选中在座调查员却开不了追逐」：旧表单提交 `character:<id>`，后端按裸 id 比对失败；现在前后端都去前缀，并兼容 `quarry`/`quarries`、`pursuer`/`pursuers` 与列表/逗号串。
- 统一 KP 工作台控件：console dock 内的单行 input 固定 36px，与 Select / MultiSelect 同高；追逐表单改为两行等宽字段，数字框统一 112px；新增 `KpStartCombatForm`、`KpStartChaseForm`，不再在 `KpActionForm` 里拼大小不一的裸控件；`KpChaseStage` 的障碍输入也统一高度。
- 验证：后端 2528 passed / 11 skipped、ruff 通过；前端 77 个测试文件 523 项、typecheck、Vite build、oxlint 0 errors；真实 KP 会话浏览器验收开战/追逐表单（截获请求确认 payload，未写库），所有单行控件高度一致、无横向溢出。

## 2026-10-17 追逐发起后主台不出现

- 现象：真人 KP 发起追逐后，追逐态已落库，刷新页面能看到追逐台；但当前页面不切换，底栏只停在原写作台。
- 根因：发起动作的 HTTP 成功只返回 `result`，页面依赖 `/live` 的 `chase_start` 事件更新 `chase`；SSE 断线或该条事件丢失时，页面没有主动重拉权威快照，直到刷新才从 `/sync` 里读到追逐态。
- 修复：新增 `useKpPrefill.bumpSync`，KP 动作成功后由写作台发出完成信号；游戏页订阅该信号并调用 `resyncState()` 重拉 `/sync`，用服务端快照兜底战斗/追逐的起停，不再只赌单条广播。
- 验证：在浏览器里把 `/live` 请求全部 abort（模拟实时事件丢失）后发起追逐，主台仍能自动出现；随后用 API 中止追逐，库中 `chase_states` 归零。前端 typecheck / 523 项测试 / build / oxlint 均通过。

## 2026-10-17 追逐台羊皮纸主题适配

- 暗色追逐台的主背景此前是硬编码深绿径向渐变，羊皮纸主题下深褐正文与距离轨标签几乎读不出来。
- 羊皮纸下把追逐主台改成浅纸底 + 极淡行线的追迹纸，补页框内高光与柔和纸面阴影；距离轨加浅色纸带、节点纸面阴影，当前节点改用暗红描边替代金色辉光。
- 逃方 / 追方棋子改为浅蓝 / 浅红纸片配深墨图标；VS 卡、逐轮记录、本轮障碍输入与分段按钮统一到纸面 token。暗色主题保持原样，所有覆盖限定在 `:root[data-theme="parchment"]`。
- 验证：1600×1000 真实追逐态羊皮纸截图检查标题、距离轨、卡面、棋子与逐轮记录对比度；前端 typecheck、523 项测试、Vite build、oxlint 通过。

## 2026-10-17 Agent 运行时解耦（refactor/agent-runtime）

- 分支 `refactor/agent-runtime`：把 AI 队友从 KP 的裁定计划里解耦。队友上下文不再注入 `TurnPlan.direction` / `team_guidance`；队友先行动，planner 移到队友之后只跑一次（不再有二次 planner）；队友决策只依赖公开场景、自身人设、私有记忆与最近事件。
- 队友并发改为**完成即提交**：不再等 `asyncio.gather` 收齐所有队友才发第一个气泡；整轮设 `TEAM_AGENT_TIMEOUT_SECONDS=20`，慢请求按 silent 处理，避免一个队友拖住 KP。事件按完成顺序落库，测试覆盖超时收束。
- 统一 NPC 对话出口：新增 `NpcRuntime`（人设 + 私有记忆 + NPC 可见事件 + 统一落库/广播/互动史），`npc_act` 委托给它；生产 LLM 路径下 `say` 拒绝有名 NPC，提示改用 `npc_act`，避免 KP 的 truth/线索/计划污染 NPC 台词。工具说明同步更新。
- 队友/NPC 事件窗口加 token 上限：队友 12k、NPC 8k，至少保留 6 条；不再只按 30/20 条粗筛，长旁白不会把上下文撑爆。
- 战斗中的关键 NPC 也读取同一份 NPC 私有记忆，避免平时记得的承诺/被看穿在进入战斗后突然失忆；对话/战斗/幕后仍未合并成同一个完整运行时。
- 新增 `agent_trace` 结构化日志：NPC / 队友调用记录 session、agent、耗时、token usage、timeout/outcome，先供运维 grep，不新增表。
- `NpcRuntime` 增加 30 秒回应超时 `NpcResponseTimeout`，超时不落 NPC 台词、不阻塞 KP 工具循环；新增超时与轨迹测试。
- 新增 `NpcState` 只读状态视图：对话/战斗共享同一份身份、人设、位置与私有记忆；幕后仍保留独立世界推演模式。
- 旧 [SAY] / 分头路径接上 `NpcDialogueGuard`：扫描器只把真实说话人放在私有 metadata，过滤器在出站前剥掉；有名 NPC 的显式台词改由 NpcRuntime 生成并按原偏移回写，不再让 KP 的全局上下文直接代言。
- 分头行动接入工具循环：每组独立上下文 + `dice_scope` 限定群检候选域，工具即时执行；旧的合并文本 `_process_commands` 只在无工具 Provider 下兜底。
- 新增 `ContextBundle` 通用上下文对象；NpcRuntime/队友决策先接入，系统提示、消息、预算、可见范围有了统一形状。
- 队友 relevance gate：玩家点名时，分处异地且本场景无新事件的未点名队友不再白跑主模型；无点名时保持全员。
- 队友并发冲突兜底：同动作且长文本高度重合的后完成决策按 silent 处理，避免两人同时说同一段话。
- `agent_trace` 落表：新增 `agent_trace_logs` 表与 Alembic 迁移、`list_for_session`、房主专用 `/agent-traces` 接口；真人 KP 页面增加「调用轨迹」抽屉（Agent/结果/耗时/token）。
- 验证：后端 2534 passed / 11 skipped、ruff 通过；前端 typecheck、523 项 vitest、Vite build、文件行数检查通过，新面板 oxlint 0 warning。
- P0 收尾：有名 NPC 漏写进旁白的署名台词不再被旧同步兜底抽成 KP 台词，直接丢弃；`ContextBundle.fit` 真正按预算裁最旧消息；`NpcState` 按 active flags 解析当前形态。
- trace 覆盖 planner/backstage/combat，增加会话级 800 条保留裁剪与房主接口授权测试；分头工具事件补分组 metadata 与事件重排。
- 验证更新：后端 2538 passed / 11 skipped、ruff 通过。
- 新增 ExecutionScope：分头组声明 group/scene/actor 边界；scene_change/start_combat/start_chase 不再在组内直接执行，改为 CoordinatorIntent，由回合协调器统一接受/拒绝（重复请求提示忽略，非主队组拒绝切场景/追逐）。
- 新增 TeamIntent/resolve_team_intents：队友决策带 observes_seq 与 target；act/assist/check 对同一 target 的并发写只保留先提交者，speak/silent 仍完成即提交；TeamAgent 输出格式补充 target 语义。
- 验证：后端 2540 passed / 11 skipped、ruff 通过。
- TeamAgent 输出新增 verb/depends_on；TeamIntent 解析中文/英文动词别名，互斥矩阵覆盖 open/block/lock/take/give/attack/protect 等；依赖按拓扑排序，缺失/成环依赖整支丢弃。
- 验证更新：后端 2546 passed / 11 skipped、ruff 通过。
- 新增 `intent_ledger`：target 带跨回合 version 与 locked/open/closed/blocked/taken 状态；队友动作提交前检查 open 遇 locked、enter 遇 blocked 等前置，成功后更新账本。
- 验证更新：后端 2550 passed / 11 skipped、ruff 通过（建卡随机用例首轮偶发失败，单独重跑通过）。
- target 别名收敛到已有对象 key（“门”命中“地下室的门”）；提交前比较 target version，旧版本意图会记录 stale 日志并在前置不满足时丢弃。
- 意图裁决冲突写入 agent_trace（agent=intent, outcome=conflict）；KP 轨迹面板新增“裁决”标签。
- 验证更新：后端 2552 passed / 11 skipped、ruff 通过（建卡随机用例单独重跑通过）；前端 typecheck 通过。
- 新增 object_registry：模组场景/NPC 解析为 scene:<id>/npc:<id> 稳定 target，世界对象可注册 aliases；TeamIntent 优先用它，找不到才回退自由文本。
- 验证更新：后端 2556 passed / 11 skipped、ruff 通过；前端 523 vitest / build 通过。
- object_registry 改为精确匹配，避免「门」误命中「门厅」；npc_act 增加可选 target/verb，NPC 物理动作成功后可写入 intent_ledger，纯对话不污染状态。
- 验证更新：后端 2557 passed / 11 skipped、ruff 通过。
- 验证：后端 2523 passed / 11 skipped、ruff 通过；更新了 NPC 知识边界、NPC 运行时、队友并发/超时、导演信号接线等测试。已覆盖：旧 [SAY]/分头路径 NPC 边界、分头工具循环、ContextBundle、队友 relevance gate/重复冲突兜底、agent_trace 落表与 KP 面板。后续可继续做语义级冲突裁决和跨 Agent 的完整调度器。
