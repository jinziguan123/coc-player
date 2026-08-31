/**
 * 待决的幸运询价（后端 `room_sync._luck_snapshot`）。
 *
 * 为什么它需要一份快照，而战斗那几个只是「HUD 显示得对不对」：询价一发出，整条结算链
 * （物品发货、线索记账、KP 续写）就停在那儿等回答，`pending_luck` 是落在 turn_state 里的
 * 持久状态。可 `luck_offer` 事件是 log 类、**不落库**——玩家刷新或断线一次，那张卡就再也
 * 回不来了，人却还在等他拍板。实测有存档正是这么停在一次侦查检定上，之后一个事件都没有。
 *
 * **它是一份「当前状态」，不是聊天记录**：页面用独立 state 持有、在消息流末尾单独渲染。
 * 早先的版本把它 addMessage 塞进消息流，踩了两个坑——`loadHistory` 是整体替换 messages 的，
 * 而 resync 里两个请求并发，`/sync` 比 `/events` 快，塞进去的卡随后就被历史覆盖掉了；
 * 更糟的是那一条消息会把首屏标志 `firstBatchDone` 提前置真，导致随后到达的整批历史
 * 被当成「新到达」，骰子动画每次刷新都重播一遍。
 */
export interface LuckSnapshot {
  pending: boolean
  char_id?: string
  actor?: string
  skill?: string
  dice_event_id?: string
  cost?: number
  available?: number
  target?: number
}

/**
 * 从 `/sync` 快照或 `luck_offer` 广播的 metadata 归一出待决询价，没有则 null。
 *
 * 两个来源同一个形状（后端刻意对齐过），走同一个出口才不会一边改了另一边忘了。
 * `cost` 缺失时返回 null：与其渲染一张「差 0 点」点不动的卡，不如不画。
 */
export function luckSnapshotFrom(src: Record<string, unknown> | undefined | null): LuckSnapshot | null {
  if (!src) return null
  if ('pending' in src && !src.pending) return null
  const cost = Number(src.cost ?? 0)
  if (!cost) return null
  return {
    pending: true,
    char_id: String(src.char_id ?? ''),
    actor: String(src.actor ?? ''),
    skill: String(src.skill ?? ''),
    dice_event_id: String(src.dice_event_id ?? ''),
    cost,
    available: Number(src.available ?? 0),
    target: Number(src.target ?? 0),
  }
}

/**
 * `GET /sessions/{id}/sync` 的响应：sync 类状态的快照 + 事件水位线。
 *
 * 与后端 `room_sync.PROVIDERS` 一一对应——那边加一个系统，这里补一个字段。
 * `combat` 的具体形状由 CombatStage 那边的类型描述，这里只声明到调用方需要的深度，
 * 免得这个契约文件反过来依赖组件。
 */
export interface SyncSnapshot {
  seq: number
  generating: boolean
  systems: {
    combat?: { active: boolean; pending_reaction?: unknown; started_seq?: number | null }
    chase?: { active: boolean }
    turn?: { confirmed_ids: string[]; total: number; ready: boolean }
    luck?: LuckSnapshot & { id?: string }
  }
}
