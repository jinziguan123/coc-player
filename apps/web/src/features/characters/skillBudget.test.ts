import { describe, it, expect } from 'vitest'
import {
  SKILL_MAX,
  creditRatingCeiling,
  grantableDelta,
  remainingOccPoints,
} from './skillBudget'

describe('remainingOccPoints', () => {
  it('信用评级要占本职点', () => {
    // 回归：旧实现只减技能加点，信用评级白拿。医生 CR 30 等于凭空多 30 点。
    expect(remainingOccPoints(300, 100, 30)).toBe(170)
  })

  it('点数不够时如实显示负数，不悄悄兜底', () => {
    expect(remainingOccPoints(100, 90, 30)).toBe(-20)
  })
})

describe('creditRatingCeiling', () => {
  const occ = { creditMin: 9, creditMax: 60, occPoints: 300, allocated: 0 }

  it('点数充裕时以职业上限为准', () => {
    expect(creditRatingCeiling(occ)).toBe(60)
  })

  it('点数不够时压到付得起的额度', () => {
    expect(creditRatingCeiling({ ...occ, allocated: 260 })).toBe(40)
  })

  it('再紧也不低于职业下限', () => {
    // 门槛是职业的准入条件，付不起也得付——让剩余点数显示成负数，别偷偷降门槛
    expect(creditRatingCeiling({ ...occ, allocated: 299 })).toBe(9)
  })
})

describe('grantableDelta', () => {
  const base = { current: 20, alloc: 0, delta: 5, remaining: 100 }

  it('点数与上限都够就足额加', () => {
    expect(grantableDelta(base)).toBe(5)
  })

  it('剩余点数不够就只加得起的部分', () => {
    expect(grantableDelta({ ...base, remaining: 3 })).toBe(3)
  })

  it('顶到 90 就加不动了', () => {
    // 回归：旧实现只看剩余点数，能一路加到 200%
    expect(grantableDelta({ ...base, current: SKILL_MAX })).toBe(0)
    expect(grantableDelta({ ...base, current: 88 })).toBe(2)
  })

  it('两条约束取更紧的那条', () => {
    expect(grantableDelta({ ...base, current: 88, remaining: 100 })).toBe(2)
    expect(grantableDelta({ ...base, current: 20, remaining: 1 })).toBe(1)
  })

  it('减点只能退还自己加过的', () => {
    expect(grantableDelta({ current: 40, alloc: 20, delta: -5, remaining: 0 })).toBe(-5)
    expect(grantableDelta({ current: 23, alloc: 3, delta: -5, remaining: 0 })).toBe(-3)
    expect(grantableDelta({ current: 20, alloc: 0, delta: -5, remaining: 0 })).toBe(0)
  })

  it('减点不受剩余点数影响', () => {
    expect(grantableDelta({ current: 40, alloc: 20, delta: -5, remaining: -30 })).toBe(-5)
  })

  it('可以放宽上限（如导入的老角色本就超过 90）', () => {
    expect(grantableDelta({ ...base, current: 95, cap: 99 })).toBe(4)
  })
})
