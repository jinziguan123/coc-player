import { describe, expect, it } from 'vitest'
import { ACTION_CHIPS, ACTION_GROUPS, ACTION_MORE, situationActions } from './kpActions'

describe('KP 动作分组', () => {
  it('直达四组加「更多」覆盖全部动作，且每个动作只出现一次', () => {
    const all = [...ACTION_GROUPS.flatMap((g) => g.ids), ...ACTION_MORE]
    expect(new Set(all).size).toBe(all.length)
    expect(new Set(all)).toEqual(new Set(ACTION_CHIPS.map((c) => c.id)))
  })

  it('局势组按态势取舍：平时给开战与追逐，战斗中只给结束战斗，追逐中两者都不给', () => {
    const ids = ACTION_GROUPS.find((g) => g.label === '局势')!.ids
    expect(situationActions(ids, false)).toEqual(['start_combat', 'start_chase'])
    expect(situationActions(ids, true)).toEqual(['end_combat'])
    expect(situationActions(ids, false, true)).toEqual([])
  })
})
