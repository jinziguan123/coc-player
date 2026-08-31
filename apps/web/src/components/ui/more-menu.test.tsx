import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { MoreMenu } from './more-menu'

const items = [
  { label: '临场角色', onClick: vi.fn() },
  { label: '风格', onClick: vi.fn() },
]

describe('更多菜单', () => {
  it('默认收着，点开才出来', async () => {
    const user = userEvent.setup()
    render(<MoreMenu items={items} />)

    const trigger = screen.getByRole('button', { name: '更多' })
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()

    await user.click(trigger)
    expect(trigger).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('menuitem', { name: '临场角色' })).toBeInTheDocument()
  })

  it('点完就收——这些都是「打开一个面板」，没有连点的用法', async () => {
    const user = userEvent.setup()
    const onClick = vi.fn()
    render(<MoreMenu items={[{ label: '风格', onClick }]} />)

    await user.click(screen.getByRole('button', { name: '更多' }))
    await user.click(screen.getByRole('menuitem', { name: '风格' }))

    expect(onClick).toHaveBeenCalledOnce()
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  })

  it('Esc 关掉', async () => {
    const user = userEvent.setup()
    render(<MoreMenu items={items} />)
    await user.click(screen.getByRole('button', { name: '更多' }))
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  })

  it('点外面也关掉', async () => {
    const user = userEvent.setup()
    render(<div><MoreMenu items={items} /><button>别处</button></div>)
    await user.click(screen.getByRole('button', { name: '更多' }))
    await user.click(screen.getByRole('button', { name: '别处' }))
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  })

  it('一项都没有时整个不渲染——非房主席位会滤掉全部条目', () => {
    const { container } = render(<MoreMenu items={[]} />)
    expect(container).toBeEmptyDOMElement()
  })
})
