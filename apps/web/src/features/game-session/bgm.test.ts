import { describe, expect, it } from 'vitest'

import { deriveBgmMood, pickTrack, tracksForMood } from './bgm'

describe('deriveBgmMood', () => {
  const locations = [
    { current: false, mood: 'horror' },
    { current: true, mood: 'calm' },
  ]

  it('战斗压过一切', () => {
    expect(deriveBgmMood({ inCombat: true, inChase: true, locations })).toBe('combat')
  })

  it('追逐次之', () => {
    expect(deriveBgmMood({ inCombat: false, inChase: true, locations })).toBe('chase')
  })

  it('平时取我自己所在场景的氛围', () => {
    expect(deriveBgmMood({ inCombat: false, inChase: false, locations })).toBe('calm')
  })

  it('没有所在场景或场景没给氛围时回落到 explore', () => {
    expect(deriveBgmMood({ inCombat: false, inChase: false, locations: [] })).toBe('explore')
    expect(deriveBgmMood({ inCombat: false, inChase: false, locations: [{ current: true }] })).toBe('explore')
  })
})

describe('tracksForMood', () => {
  it('有本氛围的曲子就用本氛围的', () => {
    expect(tracksForMood({ horror: ['h.mp3'], explore: ['e.mp3'] }, 'horror')).toEqual(['h.mp3'])
  })

  it('本氛围没曲子回落到 explore，再没有就静音', () => {
    expect(tracksForMood({ explore: ['e.mp3'] }, 'mythos')).toEqual(['e.mp3'])
    expect(tracksForMood({}, 'mythos')).toEqual([])
  })
})

describe('pickTrack', () => {
  it('多首时不重复刚放过的', () => {
    for (const r of [0, 0.5, 0.99]) {
      expect(pickTrack(['a', 'b'], 'a', () => r)).toBe('b')
    }
  })

  it('只有一首就循环它，没有就 null', () => {
    expect(pickTrack(['a'], 'a')).toBe('a')
    expect(pickTrack([], null)).toBeNull()
  })
})
