import { render, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { localApi } from '@/api/client'
import * as netlink from '@/api/netlink'
import { useNetlinkAutoStart } from './useNetlinkAutoStart'

vi.mock('@/api/client', () => ({
  localApi: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}))

vi.mock('@/api/netlink', async (importOriginal) => {
  const actual = await importOriginal<typeof netlink>()
  return {
    ...actual,
    netlinkAvailable: vi.fn(() => true),
    netlinkStatus: vi.fn(),
    netlinkStart: vi.fn(),
  }
})

const mockGet = vi.mocked(localApi.get)
const mockAvailable = vi.mocked(netlink.netlinkAvailable)
const mockStatus = vi.mocked(netlink.netlinkStatus)
const mockStart = vi.mocked(netlink.netlinkStart)

const idle: netlink.NetlinkStatus = {
  hosting: false,
  endpoint_id: null,
  invite: null,
  connected_to: null,
  local_port: null,
  pending: [],
  approved: [],
  wanted: false,
}

function Harness() {
  useNetlinkAutoStart()
  return null
}

/**
 * 这个 hook 存在的理由：iroh endpoint 随进程消失，而房主「想开着」的意愿存了盘。
 *
 * 它**必须挂在全局**。曾经放在设置页的面板里，后果是隧道要等房主恰好翻到
 * 「设置 → 联机」才启动——客人拿着正确的房间码怎么都进不来，而房主一打开设置页
 * 对方就突然进来了。
 */
describe('内置直连的开机自动恢复', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockAvailable.mockReturnValue(true)
    mockGet.mockResolvedValue({ port: 8756 })
    mockStart.mockResolvedValue('xu4vabc')
  })

  it('上次开着就开回来，不必等房主去翻设置页', async () => {
    mockStatus.mockResolvedValue({ ...idle, wanted: true })
    render(<Harness />)
    await waitFor(() => expect(mockStart).toHaveBeenCalledWith(8756))
  })

  it('上次是显式关掉的就不自动开', async () => {
    mockStatus.mockResolvedValue({ ...idle, wanted: false })
    render(<Harness />)
    await waitFor(() => expect(mockStatus).toHaveBeenCalled())
    expect(mockStart).not.toHaveBeenCalled()
  })

  it('已经开着时不重复启动', async () => {
    mockStatus.mockResolvedValue({ ...idle, wanted: true, hosting: true })
    render(<Harness />)
    await waitFor(() => expect(mockStatus).toHaveBeenCalled())
    expect(mockStart).not.toHaveBeenCalled()
  })

  it('非桌面环境直接跳过，不去调后端', async () => {
    mockAvailable.mockReturnValue(false)
    render(<Harness />)
    expect(mockStatus).not.toHaveBeenCalled()
    expect(mockGet).not.toHaveBeenCalled()
  })

  it('拿不到后端端口时不硬启动——隧道会反代到错的地方', async () => {
    mockStatus.mockResolvedValue({ ...idle, wanted: true })
    // 开发态会回落到 8000，所以这里模拟的是「连 /net 都读不到」
    mockGet.mockRejectedValue(new Error('backend down'))
    render(<Harness />)
    await waitFor(() => expect(mockStatus).toHaveBeenCalled())
    expect(mockStart).not.toHaveBeenCalled()
  })

  it('启动失败不抛出——房主没主动操作，不该被弹错', async () => {
    mockStatus.mockResolvedValue({ ...idle, wanted: true })
    mockStart.mockRejectedValue(new Error('bind failed'))
    render(<Harness />)
    await waitFor(() => expect(mockStart).toHaveBeenCalled())
    // 走到这里没有 unhandled rejection 就算过
  })
})
