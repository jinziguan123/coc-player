import { describe, expect, it } from 'vitest'
import { pickFeatured } from './useHomeInventory'

describe('拱门里摆哪一桌', () => {
  it('取最近碰过的那桌', () => {
    const got = pickFeatured([
      { id: 'old', status: 'active', updated_at: '2026-10-01T00:00:00' },
      { id: 'new', status: 'paused', updated_at: '2026-10-13T00:00:00' },
    ])
    expect(got?.id).toBe('new')
  })

  it('已开打的优先于还在大厅的，哪怕大厅那桌更新', () => {
    const got = pickFeatured([
      { id: 'lobby', status: 'setup', updated_at: '2026-10-14T00:00:00' },
      { id: 'table', status: 'active', updated_at: '2026-10-01T00:00:00' },
    ])
    expect(got?.id).toBe('table')
  })

  it('按最后一次交互排，而不是 updated_at——后台回写会刷新后者', () => {
    const got = pickFeatured([
      { id: 'touched', status: 'active', updated_at: '2026-10-14T00:00:00', last_active_at: '2026-09-01T00:00:00' },
      { id: 'chatted', status: 'active', updated_at: '2026-07-01T00:00:00', last_active_at: '2026-10-14T13:00:00' },
    ])
    expect(got?.id).toBe('chatted')
  })

  it('一桌都没有给 null', () => {
    expect(pickFeatured([])).toBeNull()
  })
})
