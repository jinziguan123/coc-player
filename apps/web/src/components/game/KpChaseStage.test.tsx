import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { KpChaseStage } from './KpChaseStage'

const PLAYERS = '甲、丙'
const PURSUERS = '乙、丁'

describe('KP 追逐主台', () => {
  it('群体追逐把双方完整名单与代表 MOV 摆出来', () => {
    render(
      <KpChaseStage
        sessionId="s1"
        rounds={[]}
        chase={{
          round: 2, gap: 1, escape_at: 5, caught_at: -3,
          quarry: '甲', pursuer: '乙',
          quarries: ['甲', '丙'], pursuers: ['乙', '丁'],
          quarry_mov: 6, pursuer_mov: 9,
        }}
      />,
    )
    expect(screen.getByText(new RegExp(PLAYERS + '.*' + PURSUERS))).toBeInTheDocument()
    expect(screen.getByText(/群体 · 按最慢逃者/)).toBeInTheDocument()
    expect(screen.getAllByText(new RegExp(PLAYERS)).length).toBeGreaterThan(0)
    expect(screen.getByText(/代表 MOV 6/)).toBeInTheDocument()
    expect(screen.getByText(/代表 MOV 9/)).toBeInTheDocument()
  })
})
