import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { CaseFileHeader } from './CaseFileHeader'
import type { HomeInventory } from '@/features/home/useHomeInventory'

const inv = (over: Partial<HomeInventory> = {}): HomeInventory => ({
  characters: 0, modules: 0, openSessions: [], ...over,
})

const open = (inventory: HomeInventory | null) =>
  render(<MemoryRouter><CaseFileHeader inventory={inventory} /></MemoryRouter>)

describe('卷宗抬头', () => {
  it('列的是真实库存，不是仿真的档案编号', () => {
    open(inv({ characters: 7, modules: 10, openSessions: [{ id: 'a', status: 'active' }] }))
    expect(screen.getByText('7')).toBeInTheDocument()
    expect(screen.getByText('10')).toBeInTheDocument()
    expect(screen.getByText('调查员')).toBeInTheDocument()
  })

  it('一个都没有时这条变成动作，而不是播报状态', () => {
    open(inv())
    // 「还没有调查员」是状态；「建一位调查员」才是下一步该点的地方
    expect(screen.getByRole('link', { name: '建一位调查员' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '导入模组' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '开一局' })).toBeInTheDocument()
  })

  it('读屏拿到的是完整的一句，不是「调查员 7」这种半句', () => {
    open(inv({ characters: 7 }))
    expect(screen.getByRole('link', { name: '调查员 7，前往' })).toBeInTheDocument()
  })

  it('库存取不到就不显示这一行，标题照常在', () => {
    open(null)
    expect(screen.getByRole('heading', { name: 'CoC Player' })).toBeInTheDocument()
    expect(screen.queryByText('调查员')).not.toBeInTheDocument()
  })
})
