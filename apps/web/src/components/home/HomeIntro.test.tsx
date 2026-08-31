import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { HomeIntro } from './HomeIntro'

/** 这些用例查的是摊开后的内容，所以直接以展开态渲染（折叠行为另有一组用例）。 */
function setup() {
  return render(<MemoryRouter><HomeIntro defaultOpen /></MemoryRouter>)
}

describe('首页介绍的联机段落', () => {
  it('三种玩法都在：一个人、同网络、跨网络', () => {
    setup()
    expect(screen.getByText('一个人也能开')).toBeInTheDocument()
    expect(screen.getByText('同一个网络')).toBeInTheDocument()
    expect(screen.getByText('隔着网络')).toBeInTheDocument()
  })

  it('跨网络那条必须写明「桌面版」——浏览器里根本没有隧道', () => {
    // netlink 跑在 Tauri 外壳的 Rust 进程里（见 api/netlink.ts），`pnpm dev` 的浏览器里
    // 调用直接抛 NetlinkUnavailableError。漏掉这句，朋友按步骤做到最后一步才发现用不了。
    const { container } = setup()
    const item = [...container.querySelectorAll('.home-coop-item')]
      .find((el) => el.textContent?.includes('隔着网络'))
    expect(item?.textContent).toContain('桌面版')
  })

  it('说清楚只有房主要配模型——否则客人会白折腾去办 API Key', () => {
    const { container } = setup()
    const coop = container.querySelector('.home-coop')
    expect(coop?.textContent).toContain('只有房主需要配模型')
  })

  it('局域网那条要讲明默认是关着的，别让人以为装完就能连', () => {
    const { container } = setup()
    const item = [...container.querySelectorAll('.home-coop-item')]
      .find((el) => el.textContent?.includes('同一个网络'))
    // 钉的是「说清楚它默认关着」这件事，不是某种特定措辞——改写句子不该误报
    expect(item?.textContent).toMatch(/默认.{0,3}关/)
  })

  it('通篇不得出现 emoji：图标一律走矢量图标库（见 CLAUDE.md）', () => {
    const { container } = setup()
    // 杂项符号、颜文字、装饰符号、变体选择符
    const emoji = /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{FE0F}]/u
    expect(container.textContent ?? '').not.toMatch(emoji)
  })
})

describe('该不该摊开', () => {
  const open = (props = {}) => render(<MemoryRouter><HomeIntro {...props} /></MemoryRouter>)

  it('默认收起——首页是卷宗，不是教学页', () => {
    open()
    expect(screen.getByRole('button', { name: /第一次跑团/ })).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('什么是跑团')).not.toBeInTheDocument()
  })

  it('还没上过桌的人自动摊开——对他这不是噪音，是刚需', () => {
    open({ defaultOpen: true })
    expect(screen.getByText('什么是跑团')).toBeInTheDocument()
  })

  it('库存迟一步点完时也能摊开（异步到达）', () => {
    const { rerender } = open()
    expect(screen.queryByText('什么是跑团')).not.toBeInTheDocument()
    rerender(<MemoryRouter><HomeIntro defaultOpen /></MemoryRouter>)
    expect(screen.getByText('什么是跑团')).toBeInTheDocument()
  })

  it('老玩家想看也点得开', async () => {
    const user = userEvent.setup()
    open()
    await user.click(screen.getByRole('button', { name: /第一次跑团/ }))
    expect(screen.getByText('什么是跑团')).toBeInTheDocument()
  })
})
