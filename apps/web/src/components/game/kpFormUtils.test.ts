import { describe, expect, it } from 'vitest'

import { characterIds, splitCsv, stripNpcRef } from './kpFormUtils'

describe('KP 表单清单', () => {
  it('逗号 / 中文顿号 / 空项都能收成数组', () => {
    expect(splitCsv(' a, b ，c、 d,, ')).toEqual(['a', 'b', 'c', 'd'])
    expect(splitCsv('')).toEqual([])
  })

  it('角色引用去掉 character: 前缀；NPC 引用去掉 npc: 前缀', () => {
    expect(characterIds('character:a,char:b,c')).toEqual(['a', 'b', 'c'])
    expect(stripNpcRef('npc:guard')).toBe('guard')
  })
})
