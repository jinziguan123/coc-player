import { describe, it, expect } from 'vitest'
import { damageBonus, deriveStats, moveAgePenalty } from './cocDerived'

/**
 * 与 server/tests/test_coc_chargen.py 逐条对齐——这两份实现漂了就是规则错了。
 */

describe('移动力', () => {
  const mov = (str: number, dex: number, siz: number) =>
    deriveStats({ STR: str, DEX: dex, SIZ: siz, CON: 50, POW: 50 }, 25).mov

  it('两者都高于体型是 9', () => {
    // 回归：旧实现第二档写成 `dex >= siz || str >= siz`，把第三档吞了，9 永远取不到
    expect(mov(80, 80, 40)).toBe(9)
  })

  it('两者都低于体型是 7', () => {
    expect(mov(30, 30, 80)).toBe(7)
  })

  it('一高一低是 8', () => {
    expect(mov(80, 30, 50)).toBe(8)
    expect(mov(50, 80, 50)).toBe(8)   // 等于体型不算「大于」
  })
})

describe('年龄减速', () => {
  it.each([
    [25, 0], [39, 0], [40, 1], [55, 2], [65, 3], [72, 4], [85, 5],
  ])('%i 岁减 %i', (age, penalty) => {
    expect(moveAgePenalty(age)).toBe(penalty)
  })

  it('叠加到移动力上', () => {
    const attrs = { STR: 80, DEX: 80, SIZ: 40, CON: 50, POW: 50 }
    expect(deriveStats(attrs, 25).mov).toBe(9)
    expect(deriveStats(attrs, 45).mov).toBe(8)
    expect(deriveStats(attrs, 85).mov).toBe(4)
  })
})

describe('伤害加值 / 体格', () => {
  it.each([
    [2, '-2', -2], [64, '-2', -2],
    [65, '-1', -1], [84, '-1', -1],
    [85, '0', 0], [124, '0', 0],
    [125, '1D4', 1], [164, '1D4', 1],
    [165, '1D6', 2], [204, '1D6', 2],
    [205, '2D6', 3], [284, '2D6', 3],
    [285, '3D6', 4], [364, '3D6', 4],
    [365, '4D6', 5], [444, '4D6', 5],
  ])('STR+SIZ=%i → %s / 体格 %i', (combined, db, build) => {
    expect(damageBonus(combined)).toEqual({ db, build })
  })

  it('超出表尾每 80 点续进一档', () => {
    // 回归：旧实现 165 以上全归 1D6
    expect(damageBonus(500)).toEqual({ db: '5D6', build: 6 })
    expect(damageBonus(600)).toEqual({ db: '6D6', build: 7 })
  })
})

describe('派生值主干', () => {
  it('HP/MP/SAN/闪避公式', () => {
    const d = deriveStats({ STR: 50, CON: 60, SIZ: 70, DEX: 51, POW: 55 }, 25)
    expect(d.hp).toBe(13)      // (60+70)/10
    expect(d.mp).toBe(11)      // 55/5
    expect(d.san).toBe(55)     // = POW
    expect(d.dodge).toBe(25)   // DEX/2 向下取整
  })

  it('属性缺失时按 50 兜底', () => {
    expect(deriveStats({}, 25)).toEqual(
      { hp: 10, mp: 10, san: 50, mov: 8, db: '0', build: 0, dodge: 25 },
    )
  })
})
