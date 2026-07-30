import { render, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { EVENT_DISCONNECTED, EVENT_PENDING } from '@/api/netlink'
import { useKnockNotices } from './useKnockNotices'

const navigate = vi.fn()
vi.mock('react-router-dom', () => ({ useNavigate: () => navigate }))

vi.mock('sonner', () => ({
  toast: Object.assign(vi.fn(() => 'toast-id'), {
    error: vi.fn(),
    success: vi.fn(),
    dismiss: vi.fn(),
  }),
}))

const setServerUrl = vi.fn()
let currentServerUrl = ''
vi.mock('@/api/client', () => ({
  getServerUrl: () => currentServerUrl,
  setServerUrl: (url: string) => setServerUrl(url),
}))

/** 事件名 → 注册进来的处理函数，供用例手动触发。 */
const handlers = new Map<string, (payload: unknown) => void>()
vi.mock('@/api/netlink', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/netlink')>()
  return {
    ...actual,
    listenNetlink: vi.fn(async (event: string, handler: (p: unknown) => void) => {
      handlers.set(event, handler)
      return () => handlers.delete(event)
    }),
    netlinkApprove: vi.fn(),
    netlinkReject: vi.fn(),
  }
})

function Harness() {
  useKnockNotices()
  return null
}

async function mount() {
  render(<Harness />)
  await waitFor(() => expect(handlers.has(EVENT_DISCONNECTED)).toBe(true))
}

describe('内置直连的全局提示', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    handlers.clear()
    currentServerUrl = ''
  })

  it('断线时切回本机——否则前端会一直对着一个没人监听的端口', async () => {
    // 症状是「会话列表空了、加入房间也进不去」，看着像存档没了，
    // 其实数据在房主库里，只是每个请求都打向死地址。
    currentServerUrl = 'http://127.0.0.1:54321'
    await mount()

    handlers.get(EVENT_DISCONNECTED)?.('host-key')
    expect(setServerUrl).toHaveBeenCalledWith('')
    // 当前页面的数据全来自房主，留在原地只会满屏报错
    expect(navigate).toHaveBeenCalledWith('/game')
  })

  it('本来就在本机时不动地址、也不跳转', async () => {
    currentServerUrl = ''
    await mount()

    handlers.get(EVENT_DISCONNECTED)?.('host-key')
    expect(setServerUrl).not.toHaveBeenCalled()
    expect(navigate).not.toHaveBeenCalled()
  })

  it('有人敲门时弹出可直接处置的提示', async () => {
    const { toast } = await import('sonner')
    await mount()

    handlers.get(EVENT_PENDING)?.({ peer_id: 'peer-a', claimed_label: '阿强' })
    expect(toast).toHaveBeenCalledWith(
      '阿强 请求加入',
      expect.objectContaining({ description: expect.stringContaining('自称') }),
    )
  })

  it('没自报名字时用公钥短名称呼', async () => {
    const { toast } = await import('sonner')
    await mount()

    handlers.get(EVENT_PENDING)?.({ peer_id: 'xu4vstranger9999', claimed_label: '' })
    expect(toast).toHaveBeenCalledWith(
      expect.stringContaining('xu4vst'),
      expect.objectContaining({ description: expect.stringContaining('没有填名字') }),
    )
  })
})
