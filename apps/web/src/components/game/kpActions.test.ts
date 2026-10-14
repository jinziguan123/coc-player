import { describe, expect, it } from 'vitest'
import { ACTION_CHIPS, ACTION_GROUPS } from './kpActions'

describe('KP 动作分组', () => {
  it('分组只是换排法，一个动作都不藏：每个直达按钮恰好落在一组里', () => {
    const grouped = ACTION_GROUPS.flatMap((g) => g.ids)
    expect(new Set(grouped).size).toBe(grouped.length)
    expect(new Set(grouped)).toEqual(new Set(ACTION_CHIPS.map((c) => c.id)))
  })
})
