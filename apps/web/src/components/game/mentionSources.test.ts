import { describe, expect, it } from 'vitest'

import { buildCandidates, filterCandidates, type MentionCandidate } from './mentionSources'

const seats = [
  { character_id: 'c1', character_name: '艾琳', role: 'human' },
  { character_id: 'c2', character_name: 'Sarah', role: 'ai' },
  { character_id: null, character_name: 'KP', role: 'kp' },
]

const locations = [
  { id: 'l1', name: '旧宅门厅', current: false, visited: true },
  { id: 'l2', name: '地下室', current: true, visited: true },
]

describe('buildCandidates', () => {
  it('队友、地点、技能都进候选，KP 席不进', () => {
    const list = buildCandidates({ seats, locations, skills: { 侦查: 70 } })
    expect(list.map((c) => c.label)).toEqual(['艾琳', 'Sarah', '地下室', '旧宅门厅', '侦查'])
  })

  it('当前所在的地点排在该组最前', () => {
    const list = buildCandidates({ seats: [], locations, skills: {} })
    expect(list[0].label).toBe('地下室')
    expect(list[0].hint).toBe('当前')
    expect(list[1].hint).toBe('已到过')
  })

  it('技能带上值作行尾提示', () => {
    const list = buildCandidates({ seats: [], locations: [], skills: { 图书馆使用: 45 } })
    expect(list[0]).toMatchObject({ kind: 'skill', label: '图书馆使用', hint: '45' })
  })

  it('重名只留一条；名字里带 @ 的直接丢掉（会把下一次判定搅乱）', () => {
    const list = buildCandidates({
      seats: [
        { character_id: 'c1', character_name: '艾琳', role: 'human' },
        { character_id: 'c3', character_name: '艾琳', role: 'ai' },
        { character_id: 'c4', character_name: 'a@b', role: 'human' },
      ],
      locations: [], skills: {},
    })
    expect(list.map((c) => c.label)).toEqual(['艾琳'])
  })

  it('没有数据时给空表，不抛异常', () => {
    expect(buildCandidates({})).toEqual([])
  })
})

describe('filterCandidates', () => {
  const list: MentionCandidate[] = [
    { kind: 'character', label: '艾琳' },
    { kind: 'character', label: 'Sarah' },
    { kind: 'location', label: '旧宅门厅' },
    { kind: 'location', label: '地下室' },
    { kind: 'skill', label: '侦查' },
    { kind: 'skill', label: '图书馆使用' },
  ]

  it('空词给全部', () => {
    expect(filterCandidates(list, '')).toHaveLength(6)
  })

  it('前缀命中排在包含命中之前', () => {
    const hit = filterCandidates(
      [{ kind: 'skill', label: '图书馆使用' }, { kind: 'skill', label: '侦查' }],
      '图书',
    )
    expect(hit.map((c) => c.label)).toEqual(['图书馆使用'])
    const both = filterCandidates(
      [{ kind: 'skill', label: '侦查' }, { kind: 'skill', label: '侦查所' }],
      '侦',
    )
    expect(both.map((c) => c.label)).toEqual(['侦查', '侦查所'])
  })

  it('同档同名次时按分组顺序：队友 → 地点 → 技能', () => {
    const hit = filterCandidates(
      [
        { kind: 'skill', label: '侦查' },
        { kind: 'location', label: '侦查所' },
        { kind: 'character', label: '侦查员老张' },
      ],
      '侦',
    )
    expect(hit.map((c) => c.label)).toEqual(['侦查员老张', '侦查所', '侦查'])
  })

  it('拉丁名不区分大小写', () => {
    expect(filterCandidates(list, 'sar').map((c) => c.label)).toEqual(['Sarah'])
  })

  it('只命中 extra 的排最后', () => {
    const hit = filterCandidates(
      [
        { kind: 'character', label: '艾琳' },
        { kind: 'character', label: 'Sarah', extra: '记者' },
      ],
      '记者',
    )
    expect(hit.map((c) => c.label)).toEqual(['Sarah'])
  })

  it('一条都不命中时给空表', () => {
    expect(filterCandidates(list, '不存在的东西')).toEqual([])
  })
})
