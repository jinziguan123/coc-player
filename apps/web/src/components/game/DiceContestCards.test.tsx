import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { BurstCard, OpposedCard, type BurstData } from './DiceContestCards'
import type { OpposedData } from './opposedDice'

/**
 * 这两张卡是从 GameSessionPage 搬出来的。搬迁本身逐字未改，但它们要靠
 * 「历史里恰好有一次对抗/连射」才会出现在界面上，人工回归很难碰到——
 * 所以补一层渲染冒烟测试，钉住关键读数不丢。
 */

const opposed: OpposedData = {
  attacker: { name: '江户川龙牙', skill: '斗殴', target: 55, roll: 21, outcome: 'hard_success' },
  defender: { name: '打手', skill: '闪避', target: 40, roll: 78, outcome: 'fail' },
  winner: 'attacker',
  result: '命中',
}

describe('OpposedCard', () => {
  it('两方姓名、技能值与骰值都要出现', () => {
    render(<OpposedCard data={opposed} fresh={false} />)
    for (const text of ['江户川龙牙', '打手', '21', '78']) {
      expect(screen.getByText(new RegExp(text))).toBeTruthy()
    }
  })

  it('渲染对抗结论', () => {
    render(<OpposedCard data={opposed} fresh={false} />)
    expect(screen.getByText(/命中/)).toBeTruthy()
  })

  it('无守方检定时降级为单侧命中卡，不炸', () => {
    const oneSided: OpposedData = { ...opposed, defender: null }
    expect(() => render(<OpposedCard data={oneSided} fresh={false} />)).not.toThrow()
  })
})

describe('BurstCard', () => {
  const burst: BurstData = {
    weapon: '点三八左轮',
    shots: [
      { hit: true, roll: 12, damage: 5, penalty: 0 },
      { hit: false, roll: 88, damage: 0, penalty: 0 },
      { hit: true, roll: 30, damage: 4, penalty: 0 },
    ] as BurstData['shots'],
  }

  it('列出武器、命中数与总伤害', () => {
    const { container } = render(<BurstCard data={burst} fresh={false} />)
    expect(screen.getByText(/点三八左轮/)).toBeTruthy()
    // 3 发里命中 2 发，合计 9 点伤害——这两个汇总数是这张卡的存在理由
    expect(container.textContent).toContain('3发 · 命中2 · 合计9伤')
  })

  it('每一发都渲染出来', () => {
    const { container } = render(<BurstCard data={burst} fresh={false} />)
    expect(container.textContent).toContain('12')
    expect(container.textContent).toContain('88')
    expect(container.textContent).toContain('30')
  })
})
