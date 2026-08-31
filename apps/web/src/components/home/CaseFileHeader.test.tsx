import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/api/client'
import { CaseFileHeader } from './CaseFileHeader'

vi.mock('@/api/client', () => ({ api: { get: vi.fn() }, getServerUrl: () => '' }))
const get = vi.mocked(api.get)

function mock({ chars = 0, modules = 0, sessions = [] as { status: string }[] } = {}) {
  get.mockImplementation((url: string) => {
    if (url === '/characters') return Promise.resolve(Array(chars).fill({}))
    if (url === '/modules') return Promise.resolve(Array(modules).fill({}))
    return Promise.resolve(sessions)
  })
}

const open = () => render(<MemoryRouter><CaseFileHeader /></MemoryRouter>)

beforeEach(() => vi.clearAllMocks())

describe('卷宗抬头', () => {
  it('列的是真实库存，不是仿真的档案编号', async () => {
    mock({ chars: 7, modules: 10, sessions: [{ status: 'active' }] })
    open()
    expect(await screen.findByText('7')).toBeInTheDocument()
    expect(screen.getByText('10')).toBeInTheDocument()
    expect(screen.getByText('调查员')).toBeInTheDocument()
  })

  it('只数还开着的局——已收场的不该算在「在跑」里', async () => {
    mock({ sessions: [{ status: 'active' }, { status: 'finished' }, { status: 'setup' }] })
    open()
    const link = await screen.findByRole('link', { name: /在跑/ })
    expect(link).toHaveTextContent('2')
  })

  it('一个都没有时这条变成动作，而不是播报状态', async () => {
    mock()
    open()
    // 「还没有调查员」是状态；「建一位调查员」才是下一步该点的地方
    expect(await screen.findByRole('link', { name: '建一位调查员' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '导入模组' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '开一局' })).toBeInTheDocument()
  })

  it('读屏拿到的是完整的一句，不是「调查员 7」这种半句', async () => {
    mock({ chars: 7 })
    open()
    expect(await screen.findByRole('link', { name: '调查员 7，前往' })).toBeInTheDocument()
  })

  it('取不到就不显示这一行，首页其余部分照常', async () => {
    get.mockRejectedValue(new Error('网络断了'))
    open()
    expect(await screen.findByRole('heading', { name: 'CoC Player' })).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByText('调查员')).not.toBeInTheDocument())
  })
})
