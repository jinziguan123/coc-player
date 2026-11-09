import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { DiceMessage } from './DiceMessage'

const base = { actor: '沃什', skill: '聆听', skill_value: 60, target: 60 }

describe('骰子卡的幸运注记', () => {
  it('原卡保留原掷失败，并注明已燃运重骰', () => {
    render(<DiceMessage fresh={false} msg={{
      id: 'a', type: 'dice', content: '', ts: '',
      metadata: { ...base, roll: 82, outcome: 'failure', luck_spent: 10, luck_mode: 'reroll' },
    } as never} />)
    expect(screen.getByText('82')).toBeInTheDocument()
    expect(screen.getByText(/已燃运重骰（烧 10 点）/)).toBeInTheDocument()
  })

  it('幸运记录写明烧了几点、原来掷了多少，不再像普通检定卡', () => {
    render(<DiceMessage fresh={false} msg={{
      id: 'b', type: 'dice', content: '', ts: '',
      metadata: { ...base, roll: 47, roll_before: 82, outcome: 'success',
        luck_spend: true, luck_mode: 'reroll', cost: 10, luck_spent: 10 },
    } as never} />)
    expect(screen.getByText(/燃运重骰 · 烧 10 点幸运 · 原掷 82/)).toBeInTheDocument()
  })

  it('补差额的记录写成改判', () => {
    render(<DiceMessage fresh={false} msg={{
      id: 'c', type: 'dice', content: '', ts: '',
      metadata: { ...base, roll: 60, roll_before: 65, outcome: 'success',
        luck_spend: true, luck_mode: 'spend', cost: 5, luck_spent: 5 },
    } as never} />)
    expect(screen.getByText(/花 5 点幸运改判 · 原掷 65/)).toBeInTheDocument()
  })

  it('普通检定卡没有幸运注记', () => {
    render(<DiceMessage fresh={false} msg={{
      id: 'd', type: 'dice', content: '', ts: '',
      metadata: { ...base, roll: 30, outcome: 'success' },
    } as never} />)
    expect(screen.queryByText(/幸运/)).toBeNull()
  })
})
