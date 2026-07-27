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
