import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { MobileNav } from './MobileNav'

function renderNav(initialEntry = '/') {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <MobileNav />
    </MemoryRouter>,
  )
}

describe('MobileNav 手机底部导航', () => {
  it('六个一级入口都在，且指向各自路由', () => {
    renderNav()
    const expected = [
      ['卷宗', '/'],
      ['模组', '/modules'],
      ['规则', '/rules'],
      ['角色', '/characters'],
      ['游戏', '/game'],
      ['设置', '/settings'],
    ] as const
    for (const [label, href] of expected) {
      const link = screen.getByRole('link', { name: label })
      expect(link).toHaveAttribute('href', href)
    }
  })

  it('当前路由对应项高亮，其余不高亮', () => {
    renderNav('/characters')
    expect(screen.getByRole('link', { name: '角色' })).toHaveClass('active')
    expect(screen.getByRole('link', { name: '卷宗' })).not.toHaveClass('active')
    expect(screen.getByRole('link', { name: '设置' })).not.toHaveClass('active')
  })

  it('首页只在精确路径高亮，子路由不算', () => {
    renderNav('/characters')
    expect(screen.getByRole('link', { name: '卷宗' })).not.toHaveClass('active')
  })
})
