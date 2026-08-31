import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { VillageRulesSummary } from './VillageRulesSummary'
import { ruleDiffLabels } from '@/lib/villageRules'

// 玩家来 /rulebooks 只想知道一件事：我在什么规则下掷骰。
// 所以摘要只列**与规则书原文不同**的项——没改过的写出来就是噪音。

describe('规矩摘要', () => {
  it('只列改过的，没改的一个不提', () => {
    expect(ruleDiffLabels({ critical_max: 3 })).toEqual(['大成功 ≤ 3'])
    expect(ruleDiffLabels({})).toEqual([])
  })

  it('相关项合并成一条，不让「幸运消费」和它的三个细则各占一格', () => {
    expect(ruleDiffLabels({
      luck_spend: true, luck_spend_max: 20, luck_spend_in_combat: false,
    })).toEqual(['幸运消费（单次 ≤ 20 点、战斗中不可用）'])
  })

  it('燃运重骰标出「本桌自定」——玩家有权知道哪条不是规则书里的', () => {
    expect(ruleDiffLabels({ luck_reroll: true, luck_reroll_cost: 10 })[0]).toContain('本桌自定')
  })

  it('照原文跑时明说，别留一片空白让人猜', () => {
    render(<VillageRulesSummary options={{}} notes="" enabled />)
    expect(screen.getByText(/完全照规则书原文跑/)).toBeInTheDocument()
  })

  it('停用时不列改动——它们此刻不算数', () => {
    render(<VillageRulesSummary options={{ critical_max: 3 }} notes="" enabled={false} />)
    expect(screen.queryByText('大成功 ≤ 3')).not.toBeInTheDocument()
    expect(screen.getByText(/村规已停用/)).toBeInTheDocument()
  })

  it('桌面约定单独一块，写明它不改判定', () => {
    render(<VillageRulesSummary options={{}} notes="重调查轻战斗" enabled />)
    expect(screen.getByText(/重调查轻战斗/)).toBeInTheDocument()
    expect(screen.getByText(/只影响叙述，不改判定/)).toBeInTheDocument()
  })
})
