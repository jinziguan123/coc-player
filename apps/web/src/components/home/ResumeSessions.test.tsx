import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { ResumeSessions } from './ResumeSessions'
import type { SessionSummary } from '@/features/game-setup/types'

const s = (over: Partial<SessionSummary> = {}): SessionSummary => ({
  id: 'sess-1', status: 'active', module_title: '鬼屋', character_name: '陈守一', ...over,
})

const open = (sessions: SessionSummary[]) =>
  render(<MemoryRouter><ResumeSessions sessions={sessions} /></MemoryRouter>)

describe('接着玩', () => {
  it('一步回到那局，不必先进「开始游戏」再翻列表', () => {
    open([s()])
    expect(screen.getByRole('link', { name: /鬼屋/ })).toHaveAttribute('href', '/game/sess-1')
  })

  it('大厅中的局回房间等人，不直接进桌', () => {
    open([s({ status: 'setup', id: 'sess-2' })])
    expect(screen.getByRole('link', { name: /鬼屋/ })).toHaveAttribute('href', '/room/sess-2')
  })

  it('一桌都没开着时整块不渲染——首页不该摆个空框', () => {
    const { container } = open([])
    expect(container).toBeEmptyDOMElement()
  })

  it('超过四桌就收口，剩下的指去游戏页——首页是入口不是列表页', () => {
    open(Array.from({ length: 6 }, (_, i) => s({ id: `s${i}` })))
    expect(screen.getAllByRole('link', { name: /鬼屋/ })).toHaveLength(4)
    expect(screen.getByRole('link', { name: '还有 2 桌' })).toHaveAttribute('href', '/game')
  })

  it('缺字段时给得出占位，不渲染半截卡', () => {
    open([s({ module_title: undefined, character_name: undefined })])
    expect(screen.getByText('未知模组')).toBeInTheDocument()
    expect(screen.getByText('未指定角色')).toBeInTheDocument()
  })
})
