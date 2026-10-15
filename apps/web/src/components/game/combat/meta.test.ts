import { describe, expect, it } from 'vitest'

import { CONDITION_META, GENERIC_CONDITION_ICON, badgesFor, conditionBadges, turnsUntil } from './meta'
import type { Combatant, CombatStatus } from './types'

const 人 = (id: string, status: CombatStatus = 'ok'): Combatant => ({
  id, name: id, side: id.startsWith('e') ? 'enemy' : 'player',
  is_human: true, hp: status === 'dead' ? 0 : 10, max_hp: 10, status,
})

describe('turnsUntil', () => {
  const 序 = [人('a'), 人('b'), 人('c'), 人('d')]

  it('轮到自己时为 0——调用方据此不显示「还差几个」', () => {
    expect(turnsUntil(序, 'b', 'b')).toBe(0)
  })

  it('顺着先攻序往后数', () => {
    expect(turnsUntil(序, 'a', 'b')).toBe(1)
    expect(turnsUntil(序, 'a', 'd')).toBe(3)
  })

  it('绕回下一轮也要数对——这是最容易写错的一处', () => {
    expect(turnsUntil(序, 'c', 'a')).toBe(2)   // c → d → a
    expect(turnsUntil(序, 'd', 'c')).toBe(3)   // d → a → b → c
  })

  it('已出局者不占位：中间死了人，距离要跟着缩短', () => {
    const 有死人 = [人('a'), 人('b', 'dead'), 人('c'), 人('d')]
    expect(turnsUntil(有死人, 'a', 'c')).toBe(1)   // b 不再占一格
    expect(turnsUntil(有死人, 'c', 'a')).toBe(2)   // c → d → a
  })

  it('自己或当前行动者不在存活序列里 → 0（不显示），不返回负数也不崩', () => {
    expect(turnsUntil([人('a'), 人('me', 'dead')], 'a', 'me')).toBe(0)
    // 当前行动者刚被打死、广播还没跟上
    expect(turnsUntil([人('a', 'dead'), 人('b')], 'a', 'b')).toBe(0)
    expect(turnsUntil(序, null, 'a')).toBe(0)
    expect(turnsUntil(序, 'a', null)).toBe(0)
  })
})

describe('conditionBadges', () => {
  const 带状态 = (conditions: string[], condition_labels?: Record<string, string>): Combatant => ({
    ...人('a'), conditions, condition_labels,
  })

  it('内置状态用专属图标与中文名', () => {
    const [b] = conditionBadges(带状态(['bleeding'], { bleeding: '流血' }))
    expect(b).toEqual({ key: 'bleeding', label: '流血', Icon: CONDITION_META.bleeding.Icon })
  })

  it('插件新增的状态不再被滤掉：通用徽标 + 组件声明的中文名', () => {
    const [b] = conditionBadges(带状态(['plugin_dazed'], { plugin_dazed: '眩晕' }))
    expect(b).toEqual({ key: 'plugin_dazed', label: '眩晕', Icon: GENERIC_CONDITION_ICON })
  })

  it('没人声明中文名时才回落到键名', () => {
    const [b] = conditionBadges(带状态(['mystery']))
    expect(b.label).toBe('mystery')
    expect(b.Icon).toBe(GENERIC_CONDITION_ICON)
  })
})

describe('badgesFor（战斗面板与角色卡共用）', () => {
  it('没有中文名表时内置状态用内置中文名', () => {
    expect(badgesFor(['bleeding'])).toEqual([{ key: 'bleeding', label: '流血', Icon: CONDITION_META.bleeding.Icon }])
  })

  it('没有状态时给空列表', () => {
    expect(badgesFor(undefined)).toEqual([])
  })
})
