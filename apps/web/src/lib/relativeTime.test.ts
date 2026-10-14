import { describe, expect, it } from 'vitest'
import { relativeTime } from './relativeTime'

const now = new Date('2026-10-14T12:00:00Z')

describe('relativeTime', () => {
  it('按量级取一个最好读的单位', () => {
    expect(relativeTime('2026-10-14T11:59:30Z', now)).toBe('刚刚')
    expect(relativeTime('2026-10-14T11:15:00Z', now)).toBe('45 分钟前')
    expect(relativeTime('2026-10-14T10:00:00Z', now)).toBe('2 小时前')
    expect(relativeTime('2026-10-11T12:00:00Z', now)).toBe('3 天前')
  })

  it('后端不带时区后缀的时间按 UTC 解读', () => {
    expect(relativeTime('2026-10-14T10:00:00', now)).toBe('2 小时前')
  })

  it('超过一周给日期，缺值或乱值给 null', () => {
    expect(relativeTime('2026-08-31T12:00:00Z', now)).toMatch(/月/)
    expect(relativeTime(undefined, now)).toBeNull()
    expect(relativeTime('not a date', now)).toBeNull()
  })
})
