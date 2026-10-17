import { describe, expect, it } from 'vitest'

import type { ChatMessage } from '@/stores/sessionStore'

import {
  buildKpLedgerEntries,
  entryMatchesFilter,
  formatLedgerTime,
  kpLedgerCounts,
  kpLedgerRole,
  replyDraft,
} from './kpLedgerModel'
import type { CombatState } from './combat/types'

const COMBAT: CombatState = {
  round: 4,
  turn: 'chen',
  order: [
    { id: 'xu', name: '许闻舟', side: 'player', is_human: true, hp: 8, max_hp: 10, status: 'ok' },
    { id: 'enemy', name: '幼体甲', side: 'enemy', is_human: false, hp: 7, max_hp: 12, status: 'ok' },
    { id: 'chen', name: '陈守一', side: 'player', is_human: true, hp: 9, max_hp: 11, status: 'ok' },
  ],
}

function message(over: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: 'm1',
    type: 'action',
    content: '我把盐撒向它的眼睛。',
    actor_name: '陈守一',
    sequence_num: 20,
    metadata: { is_player: true },
    ...over,
  }
}

describe('buildKpLedgerEntries', () => {
  it('只收本场开始之后的、桌边发生过的事', () => {
    const entries = buildKpLedgerEntries([
      message({ id: 'old', sequence_num: 5 }),
      message({ id: 'new', sequence_num: 21 }),
      message({ id: 'card', type: 'system', content: '一张手书', metadata: { kind: 'handout' } }),
      message({ id: 'kp', type: 'narration', actor_name: 'KP', content: '风灌进来。' }),
      message({ id: 'unknown', type: 'battle_only' }),
    ], COMBAT, 10)
    expect(entries.map((entry) => entry.id)).toEqual(['new', 'kp'])
    expect(entries[0].actorNo).toBe(3)
    expect(entries[0].actor?.side).toBe('player')
    expect(entries[1].kind).toBe('narration')
  })

  it('识别 KP 发起的待投检定，并保留机械结算标记', () => {
    const entries = buildKpLedgerEntries([
      message({ id: 'check', type: 'system', content: '陈守一 请投力量', metadata: { check_request: true, id: 'check-1' } }),
      message({ id: 'log', type: 'system', content: '幼体甲 HP 12 降至 7', actor_name: '战斗', metadata: { combat_log: true } }),
    ], COMBAT, null)
    expect(entries[0].pendingCheckId).toBe('check-1')
    expect(entries[1].mechanical).toBe(true)
    expect(entries[1].actor).toBeNull()
  })

  it('实时消息没有 seq 时也保留，避免刚发出的发言从账页消失', () => {
    const entries = buildKpLedgerEntries([message({ id: '', sequence_num: undefined })], COMBAT, 10)
    expect(entries).toHaveLength(1)
    expect(entries[0].id).toContain('陈守一')
  })
})

describe('entryMatchesFilter / kpLedgerCounts', () => {
  const entries = buildKpLedgerEntries([
    message({ id: 'action', type: 'action' }),
    message({ id: 'ooc', type: 'ooc', content: '（等一下）' }),
    message({ id: 'dice', type: 'dice', content: '斗殴 27/45', actor_name: '许闻舟' }),
    message({ id: 'log', type: 'system', content: '命中', actor_name: '战斗', metadata: { combat_log: true } }),
    message({ id: 'check', type: 'system', content: '请投力量', metadata: { check_request: true, id: 'check-1' } }),
  ], COMBAT, null)
  const pending = new Set(['check-1'])

  it('按玩家 / 场外 / 掷骰 / 待办筛选', () => {
    expect(entries.filter((entry) => entryMatchesFilter(entry, 'player', pending))).toHaveLength(1)
    expect(entries.filter((entry) => entryMatchesFilter(entry, 'ooc', pending))).toHaveLength(1)
    expect(entries.filter((entry) => entryMatchesFilter(entry, 'roll', pending))).toHaveLength(2)
    expect(entries.filter((entry) => entryMatchesFilter(entry, 'todo', pending))).toHaveLength(1)
  })

  it('计数里 todo 用待办区口径，其余用时间轴口径', () => {
    expect(kpLedgerCounts(entries, pending, 3)).toEqual({
      all: 5, todo: 3, player: 1, ooc: 1, roll: 2,
    })
  })
})

describe('展示辅助', () => {
  const [entry] = buildKpLedgerEntries([message()], COMBAT, null)
  it('角色小字优先用队伍职业', () => {
    expect(kpLedgerRole(entry, [{ name: '陈守一', occupation: '外科医生', isAi: false }])).toBe('外科医生')
  })
  it('场外回复预填括号', () => {
    expect(replyDraft('陈守一')).toBe('（回 陈守一：）')
  })
  it('时间格式化为 HH:mm', () => {
    expect(formatLedgerTime(new Date('2026-10-17T19:45:00+08:00').getTime())).toMatch(/^\d{2}:\d{2}$/)
  })
})
