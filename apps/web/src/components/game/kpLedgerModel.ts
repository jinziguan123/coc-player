import type { ChatMessage } from '@/stores/sessionStore'

import type { Combatant, CombatState } from './combat/types'

/** 观局台账页的条目类型。与聊天消息的 type 不是同义词：system 里还分机械结算与检定请求。 */
export type KpLedgerKind = 'narration' | 'dialogue' | 'action' | 'ooc' | 'dice' | 'system'

/** 账页筛选。todo 只看仍挂在 pending_checks 里的检定；roll 含骰子与战斗机械结算。 */
export type KpLedgerFilter = 'all' | 'todo' | 'player' | 'ooc' | 'roll'

export interface KpLedgerEntry {
  id: string
  /** 事件序号；实时乐观消息可能还没有序号 */
  seq?: number
  kind: KpLedgerKind
  content: string
  actorName: string
  /** 真人玩家（metadata.is_player），用于「玩家」筛选与行内动作 */
  isPlayer: boolean
  ts?: number
  /** 按 actor_name 对齐到的参战方；NPC/KP/系统为 null */
  actor: Combatant | null
  /** 先攻序（1-based）；未参战为 null */
  actorNo: number | null
  /** 这条 system 消息是 KP 发起的待投检定；值为 check_id */
  pendingCheckId: string | null
  /** 战斗机械结算（metadata.combat_log） */
  mechanical: boolean
  /** 骰子是否命中（metadata.hit）；非攻击骰为 null */
  hit: boolean | null
  /** 对抗结果（metadata.opposed.result），如「命中」「被闪开/防住」 */
  opposedResult: string
}

/** 这些 system 卡是给玩家看的版式卡片，不适合放进 KP 的桌边流水。 */
const IGNORED_METADATA_KINDS = new Set(['handout', 'illustration', 'module_intro', 'travel_suggest'])

const KIND_BY_TYPE: Record<string, KpLedgerKind> = {
  narration: 'narration',
  dialogue: 'dialogue',
  action: 'action',
  ooc: 'ooc',
  dice: 'dice',
  system: 'system',
}

/**
 * 把聊天消息折成账页条目：只保留本场战斗开始之后、并且属于「桌边发生过的事」的消息。
 *
 * 和 selectCombatLog 不同，这里**保留原始顺序与消息元数据**——账页要让玩家发言、场外、
 * 骰果和机械结算排在同一条时间轴上，靠的正是每条消息自己的 sequence_num。
 */
export function buildKpLedgerEntries(
  messages: ChatMessage[],
  combat: CombatState | null,
  since: number | null,
): KpLedgerEntry[] {
  const out: KpLedgerEntry[] = []
  const order = combat?.order || []
  for (const message of messages) {
    const kind = KIND_BY_TYPE[message.type]
    if (!kind) continue
    if (since != null && message.sequence_num != null && message.sequence_num <= since) continue
    const meta = (message.metadata || {}) as Record<string, unknown>
    const cardKind = typeof meta.kind === 'string' ? meta.kind : ''
    if (kind === 'system' && IGNORED_METADATA_KINDS.has(cardKind)) continue
    const actorName = (message.actor_name || (kind === 'narration' ? 'KP' : '')).trim()
    const actor = order.find((item) => item.name === actorName) || null
    const checkId = meta.check_request === true && meta.id != null ? String(meta.id) : null
    const opposed = meta.opposed && typeof meta.opposed === 'object'
      ? (meta.opposed as Record<string, unknown>)
      : null
    out.push({
      id: message.id || (message.sequence_num != null ? 'seq-' + message.sequence_num : actorName + '-' + out.length),
      seq: message.sequence_num,
      kind,
      content: message.content || '',
      actorName,
      isPlayer: !!meta.is_player,
      ts: message.ts,
      actor,
      actorNo: actor ? order.indexOf(actor) + 1 : null,
      pendingCheckId: checkId,
      mechanical: meta.combat_log === true,
      hit: typeof meta.hit === 'boolean' ? meta.hit : null,
      opposedResult: opposed && typeof opposed.result === 'string' ? opposed.result : '',
    })
  }
  return out
}

/** 筛选只影响时间轴；顶部的待办区始终显示（那里是「现在要处理什么」）。 */
export function entryMatchesFilter(
  entry: KpLedgerEntry,
  filter: KpLedgerFilter,
  pendingCheckIds: ReadonlySet<string>,
): boolean {
  switch (filter) {
    case 'all':
      return true
    case 'todo':
      return !!entry.pendingCheckId && pendingCheckIds.has(entry.pendingCheckId)
    case 'player':
      return entry.isPlayer && (entry.kind === 'action' || entry.kind === 'dialogue')
    case 'ooc':
      return entry.kind === 'ooc'
    case 'roll':
      return entry.kind === 'dice' || entry.mechanical
  }
}

export interface KpLedgerCounts {
  all: number
  todo: number
  player: number
  ooc: number
  roll: number
}

/** 筛选 chip 上的数字。todo 取待办区（待投检定 + 玩家申请），其余按时间轴条目计。 */
export function kpLedgerCounts(
  entries: KpLedgerEntry[],
  pendingCheckIds: ReadonlySet<string>,
  todoFromDesk: number,
): KpLedgerCounts {
  return {
    all: entries.length,
    todo: todoFromDesk,
    player: entries.filter((entry) => entryMatchesFilter(entry, 'player', pendingCheckIds)).length,
    ooc: entries.filter((entry) => entryMatchesFilter(entry, 'ooc', pendingCheckIds)).length,
    roll: entries.filter((entry) => entryMatchesFilter(entry, 'roll', pendingCheckIds)).length,
  }
}

/** 账页上的角色身份小字。战斗参战方没有职业字段，只能从队伍体征补。 */
export function kpLedgerRole(
  entry: KpLedgerEntry,
  party: ReadonlyArray<{ name: string; occupation: string; isAi: boolean }>,
): string {
  if (entry.kind === 'narration' && entry.actorName === 'KP') return '旁白'
  if (entry.kind === 'system') return entry.pendingCheckId ? '检定请求' : entry.mechanical ? '桌面结算' : '系统'
  const member = party.find((item) => item.name === entry.actorName)
  if (member?.occupation) return member.occupation
  if (member?.isAi) return 'AI 队友'
  if (entry.actor?.side === 'enemy') return '敌方'
  if (entry.actor?.side === 'ally') return '友方'
  if (entry.isPlayer) return '玩家'
  return entry.actor ? 'NPC' : ''
}

/** 一条消息发给谁看：给账页的「发检定」按钮做默认目标。 */
export function kpLedgerActorId(entry: KpLedgerEntry): string | null {
  if (!entry.actor) return null
  return entry.actor.side === 'enemy' ? null : entry.actor.id
}

export function formatLedgerTime(ts?: number): string {
  if (!ts || Number.isNaN(ts)) return ''
  return new Date(ts).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

/** 给「回复」预填一句话：括号内为场外，与 KP 旁白输入框的既有约定一致。 */
export function replyDraft(actorName: string): string {
  return '（回 ' + (actorName || '玩家') + '：）'
}
