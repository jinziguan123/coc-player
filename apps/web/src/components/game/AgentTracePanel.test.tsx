import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { UsageBreakdown, type UsageSummary } from './AgentTracePanel'

function u(total: number, extra: Partial<UsageSummary['traced']> = {}) {
  return {
    prompt_tokens: 0, completion_tokens: 0, reasoning_tokens: 0, total_tokens: total,
    cache_read_tokens: 0, cache_write_tokens: 0, calls: 0, ...extra,
  }
}

describe('UsageBreakdown', () => {
  it('按环节列出 token、缓存命中与思考占比', () => {
    render(<UsageBreakdown summary={{
      agents: [
        { agent: 'kp', ...u(8000, { prompt_tokens: 6000, completion_tokens: 2000, cache_read_tokens: 3000, reasoning_tokens: 500, calls: 3 }) },
        { agent: 'validator', ...u(2000, { prompt_tokens: 1800, completion_tokens: 200, calls: 1 }) },
      ],
      session_total: u(10000), since: u(0), traced: u(10000), untraced: u(0),
    }} />)
    expect(screen.getByText('KP 叙事')).toBeInTheDocument()
    expect(screen.getByText('终检')).toBeInTheDocument()
    expect(screen.getByText('50%')).toBeInTheDocument()   // 缓存命中 3000 / 6000
    expect(screen.getByText('25%')).toBeInTheDocument()   // 思考 500 / 2000
    expect(screen.getByText(/未归入环节 0（0 次调用）/)).toBeInTheDocument()
  })

  it('对账扣掉启用账本前的基线，差额明显时标红', () => {
    const { container } = render(<UsageBreakdown summary={{
      agents: [{ agent: 'kp', ...u(5000, { calls: 2 }) }],
      session_total: u(20000), since: u(10000), traced: u(5000), untraced: u(5000, { calls: 2 }),
    }} />)
    expect(screen.getByText(/各环节合计 5\.0k \/ 本局消耗 10\.0k/)).toBeInTheDocument()
    expect(container.querySelector('.trace-usage-diff.is-bad')).not.toBeNull()
  })
})
