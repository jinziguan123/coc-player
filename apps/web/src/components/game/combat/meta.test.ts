import { describe, expect, it } from 'vitest'

import { turnsUntil } from './meta'
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
