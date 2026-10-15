// 规则组件（尤其第三方插件）挂的状态，前端没有写死的条目也要显示出来，名字用组件声明的中文名。
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { CombatantCard } from './CombatantCard'
import type { Combatant } from './types'

const base: Combatant = {
  id: 'pc', name: '调查员', side: 'player', is_human: true, hp: 8, max_hp: 10, status: 'ok',
}

describe('CombatantCard 状态徽标', () => {
  it('不认识的状态显示通用徽标与组件声明的中文名，内置状态照旧', () => {
    render(
      <CombatantCard
        c={{ ...base, conditions: ['grappled', 'plugin_dazed', 'mystery'], condition_labels: { plugin_dazed: '眩晕' } }}
        mine
        active={false}
      />,
    )
    for (const label of ['被擒', '眩晕', 'mystery']) {
      const chip = screen.getByText(label, { exact: false }).closest('.chip')
      expect(chip).not.toBeNull()
      expect(chip!.querySelector('svg')).not.toBeNull()   // 每个徽标都有矢量图标
    }
  })
})
