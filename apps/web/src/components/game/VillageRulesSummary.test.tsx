import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { VillageRulesSummary } from './VillageRulesSummary'

// 玩家来 /rulebooks 只想知道一件事：我在什么规则下掷骰。
// 所以摘要只列**与规则书原文不同**的项——没改过的写出来就是噪音。

describe('规矩摘要', () => {
  // 标签本身怎么翻译（只列改过的、相关项合并、燃运重骰标「本桌自定」）由后端各规则组件
  // 生成并在 server/tests/test_house_rules.py 里钉住；这里只管摘要怎么摆。
  it('照后端给的标签逐个列出，并说清有几处不同', () => {
    render(<VillageRulesSummary options={{ critical_max: 3 }} labels={['大成功 ≤ 3']} notes="" enabled />)
    expect(screen.getByText('大成功 ≤ 3')).toBeInTheDocument()
    expect(screen.getByText(/有 1 处不同/)).toBeInTheDocument()
  })

  it('照原文跑时明说，别留一片空白让人猜', () => {
    render(<VillageRulesSummary options={{}} labels={[]} notes="" enabled />)
    expect(screen.getByText(/完全照规则书原文跑/)).toBeInTheDocument()
  })

  it('停用时不列改动——它们此刻不算数', () => {
    render(<VillageRulesSummary options={{ critical_max: 3 }} labels={['大成功 ≤ 3']} notes="" enabled={false} />)
    expect(screen.queryByText('大成功 ≤ 3')).not.toBeInTheDocument()
    expect(screen.getByText(/村规已停用/)).toBeInTheDocument()
  })

  it('桌面约定单独一块，写明它不改判定', () => {
    render(<VillageRulesSummary options={{}} labels={[]} notes="重调查轻战斗" enabled />)
    expect(screen.getByText(/重调查轻战斗/)).toBeInTheDocument()
    expect(screen.getByText(/只影响叙述，不改判定/)).toBeInTheDocument()
  })
})
