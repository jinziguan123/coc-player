import { describe, it, expect } from 'vitest'
import { assetTier, deriveAssets, splitSkill } from './useCocData'

/**
 * 信用评级换算表（CoC 7 版，1920 年代美元）。
 *
 * 这张表整个钉在这里，是因为它曾经错过一列：现金倍率比规则书高一个量级，
 * 而消费水平与资产两列是对的——单看代码很难发现，得把三列并排摆出来才看得出。
 */
describe('deriveAssets', () => {
  it.each([
    // cr,  等级,        消费水平, 现金,  资产
    [0, '一贫如洗', 0.5, 0.5, 0],
    [5, '贫穷', 2, 5, 50],
    [9, '贫穷', 2, 9, 90],
    [10, '普通', 10, 20, 500],
    [30, '普通', 10, 60, 1500],
    [49, '普通', 10, 98, 2450],
    [50, '富裕', 50, 250, 25000],
    [89, '富裕', 50, 445, 44500],
    [90, '富有', 250, 1800, 180000],
    [98, '富有', 250, 1960, 196000],
    [99, '巨富', 5000, 50000, 5000000],
  ])('信用 %i → %s', (cr, tier, spendingLevel, cash, assets) => {
    expect(deriveAssets(cr)).toEqual({ tier, spendingLevel, cash, assets })
  })

  it('现金远小于资产——现金是随身的钱，不是身家', () => {
    // 回归：现金列曾经是 ×2/×20/×50/×100，普通阶层一上来就揣着 600 刀
    for (const cr of [5, 30, 70, 95]) {
      const d = deriveAssets(cr)
      expect(d.cash).toBeLessThan(d.assets)
    }
  })

  it('等级分档与换算表一致', () => {
    expect(assetTier(0)).toBe('一贫如洗')
    expect(assetTier(9)).toBe('贫穷')
    expect(assetTier(10)).toBe('普通')
    expect(assetTier(50)).toBe('富裕')
    expect(assetTier(90)).toBe('富有')
    expect(assetTier(99)).toBe('巨富')
  })
})

describe('splitSkill', () => {
  it('拆出基名与专精', () => {
    expect(splitSkill('格斗(斗殴)')).toEqual(['格斗', '斗殴'])
    expect(splitSkill('科学(生物学)')).toEqual(['科学', '生物学'])
  })

  it('无专精时 spec 为空', () => {
    expect(splitSkill('侦查')).toEqual(['侦查', ''])
  })
})
