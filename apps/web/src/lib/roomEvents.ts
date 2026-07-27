/**
 * 房间事件的前端契约。
 *
 * `RoomEventType` 从 `generated.ts` 取，而后者由 `pnpm api:generate` 从后端
 * `app/services/room_events.py` 生成——所以类型集合的唯一真源在后端，前端不重抄一份。
 *
 * 下面的 `CATEGORY` 是一个 `Record<RoomEventType, Category>`：**后端新增一种事件而这里
 * 没有归类，`tsc --noEmit` 直接报错**。这就是把实时层的契约治理落成 CI 门禁的地方——
 * 在此之前，后端加事件、前端漏处理，不会有任何提示。
 *
 * 分类的含义见后端 `room_events.py`；三类的持久化/重放/去重规则完全不同：
 * - `stream` 流控与流式片段：不进历史、不重放、不去重，最后一条为准。
 * - `log`    叙事日志：进 `event_logs`，带 id，按 id 去重，断线后从 DB 补齐。
 * - `sync`   状态失效通知：真值在业务接口里，事件只说明「某状态变了」。
 */

import type { components } from '@/api/generated'

export type RoomEventType = components['schemas']['RoomEvent']['type']
export type RoomEvent = components['schemas']['RoomEvent']

export type EventCategory = 'stream' | 'log' | 'sync'

/**
 * 按分类切开的三个子联合。有了它们，事件分发才能写成**穷尽的 switch**：
 * 后端加一种事件而界面没处理，`default` 分支的 `never` 赋值会直接编译不过。
 * 光有 `CATEGORY` 只能保证「归了类」，保证不了「界面认得」。
 */
export type StreamEventType =
  | 'generating' | 'done' | 'ready' | 'typing' | 'presence' | 'housekeeping' | 'narration'

export type LogEventType =
  | 'dialogue' | 'action' | 'dice' | 'narration_full' | 'npc_dialogue'
  | 'system' | 'ooc' | 'check_request'

export type SyncEventType =
  | 'lobby' | 'seat' | 'started' | 'status' | 'end_vote' | 'turn_state'
  | 'character_update' | 'inventory_update'
  | 'kp_turn_ready' | 'kp_roll_ready' | 'kp_action' | 'kp_request'
  | 'event_update' | 'event_delete' | 'event_patch'
  | 'combat_start' | 'combat_state' | 'combat_reaction_prompt' | 'combat_end'
  | 'chase_start' | 'chase_state' | 'chase_end'

export type NonLogEventType = StreamEventType | SyncEventType

type Equals<A, B> = [A] extends [B] ? ([B] extends [A] ? true : false) : false

/**
 * 编译期断言：三个子联合合起来必须与后端生成的类型集合**完全相等**。
 * 多一个或少一个，右边的类型就变成 `false`，这行赋值即编译失败。
 */
export const CATEGORIES_COVER_ALL_TYPES: Equals<
  StreamEventType | LogEventType | SyncEventType,
  RoomEventType
> = true

export const CATEGORY: Record<RoomEventType, EventCategory> = {
  // 流控与流式片段
  generating: 'stream',
  done: 'stream',
  ready: 'stream',
  typing: 'stream',
  presence: 'stream',
  housekeeping: 'stream',
  narration: 'stream',
  // 叙事日志（进 event_logs）
  dialogue: 'log',
  action: 'log',
  dice: 'log',
  narration_full: 'log',
  npc_dialogue: 'log',
  system: 'log',
  ooc: 'log',
  check_request: 'log',
  // 状态失效通知
  lobby: 'sync',
  seat: 'sync',
  started: 'sync',
  status: 'sync',
  end_vote: 'sync',
  turn_state: 'sync',
  character_update: 'sync',
  inventory_update: 'sync',
  kp_turn_ready: 'sync',
  kp_roll_ready: 'sync',
  kp_action: 'sync',
  kp_request: 'sync',
  event_update: 'sync',
  event_delete: 'sync',
  event_patch: 'sync',
  combat_start: 'sync',
  combat_state: 'sync',
  combat_reaction_prompt: 'sync',
  combat_end: 'sync',
  chase_start: 'sync',
  chase_state: 'sync',
  chase_end: 'sync',
}

export function categoryOf(type: string): EventCategory | undefined {
  return CATEGORY[type as RoomEventType]
}

/** 是否为进历史、按 id 去重的持久事件。 */
export function isLogEvent(type: string): boolean {
  return categoryOf(type) === 'log'
}

/** 协议版本：与后端 `room_events.PROTOCOL_VERSION` 对应，连接房主时比对。 */
export const PROTOCOL_VERSION = 1

/**
 * 穷尽性守卫：分发的 `default`/`else` 分支调用它。
 * 若某个事件类型没有被上面的分支处理掉，`t` 在这里就不是 `never`，编译即失败。
 * 运行时什么也不做——它的价值全在编译期。
 */
export function assertAllNonLogHandled(_t: never): void {}
export function assertAllLogHandled(_t: never): void {}
