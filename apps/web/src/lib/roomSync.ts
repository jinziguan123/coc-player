import type { ChatMessage } from '../stores/sessionStore'

/**
 * 待决的幸运询价（后端 `room_sync._luck_snapshot`）。
 *
 * 为什么它需要一份快照，而战斗那几个是「HUD 显示得对不对」的问题：询价一发出，整条结算链
 * （物品发货、线索记账、KP 续写）就停在那儿等回答，`pending_luck` 是落在 turn_state 里的
 * 持久状态。可 `luck_offer` 事件是 log 类、**不落库**——玩家刷新或断线一次，那张卡就再也
 * 回不来了，人却还在等他拍板。实测有存档正是这么停在一次侦查检定上，之后一个事件都没有。
 */
export interface LuckSnapshot {
  pending: boolean
  id?: string
  char_id?: string
  actor?: string
  skill?: string
  dice_event_id?: string
  cost?: number
  available?: number
  target?: number
}

/**
 * 把快照还原成消息流里的那条幸运询价，没有待决的就返回 null。
 *
 * id 用后端给的（`luck_offer_event_id`，与检定当场那次广播同一个）——store 的
 * `addMessage` 按 id 幂等，所以在线时重连补一次也只会合成一条，不会并排出两张一样的卡。
 * 字段形状与广播时的 metadata 保持一致，两条路复用页面里同一处渲染。
 */
export function luckOfferMessage(luck: LuckSnapshot | undefined): ChatMessage | null {
  if (!luck?.pending || !luck.id || !luck.cost) return null
  return {
    id: luck.id,
    type: 'system',
    content: `${luck.actor ?? ''} 的这次检定差 ${luck.cost} 点——可花 ${luck.cost} 点幸运扭转`,
    metadata: {
      char_id: luck.char_id ?? '',
      actor: luck.actor ?? '',
      skill: luck.skill ?? '',
      dice_event_id: luck.dice_event_id ?? '',
      cost: luck.cost,
      available: luck.available,
      target: luck.target,
    },
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
    luck?: LuckSnapshot
  }
}
