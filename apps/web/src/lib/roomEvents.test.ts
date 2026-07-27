import { describe, expect, it } from 'vitest'

import { CATEGORY, categoryOf, isLogEvent, PROTOCOL_VERSION } from './roomEvents'

describe('房间事件分类', () => {
  it('三类的成员互斥且都非空', () => {
    const values = Object.values(CATEGORY)
    expect(values.filter((c) => c === 'stream').length).toBeGreaterThan(0)
    expect(values.filter((c) => c === 'log').length).toBeGreaterThan(0)
    expect(values.filter((c) => c === 'sync').length).toBeGreaterThan(0)
    expect(new Set(values)).toEqual(new Set(['stream', 'log', 'sync']))
  })

  it('进历史的持久事件归 log，流控与状态通知不归 log', () => {
    expect(isLogEvent('dialogue')).toBe(true)
    expect(isLogEvent('dice')).toBe(true)
    expect(isLogEvent('narration_full')).toBe(true)
    // 流式片段不进历史：完整叙述另以 narration_full 落库重放
    expect(isLogEvent('narration')).toBe(false)
    expect(isLogEvent('generating')).toBe(false)
    expect(isLogEvent('combat_state')).toBe(false)
  })

  it('未登记的类型返回 undefined 而不是误归类', () => {
    expect(categoryOf('replay_done')).toBeUndefined()
    expect(categoryOf('')).toBeUndefined()
  })

  it('协议版本与后端保持同一个数字', () => {
    // 后端 app/services/room_events.py 的 PROTOCOL_VERSION；不一致时客人连接会被拦下
    expect(PROTOCOL_VERSION).toBe(1)
  })
})
