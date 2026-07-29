import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { localApi } from '@/api/client'
import * as netlink from '@/api/netlink'
import { SettingsPage } from '@/pages/SettingsPage'

vi.mock('@/api/client', () => ({
  api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  localApi: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}))

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}))

vi.mock('@/api/netlink', async (importOriginal) => {
  // shortPeerId 是纯函数，保留真实实现，界面上的短名才是真的。
  const actual = await importOriginal<typeof netlink>()
  return {
    ...actual,
    netlinkAvailable: vi.fn(() => true),
    netlinkStatus: vi.fn(),
    netlinkStart: vi.fn(),
    netlinkStop: vi.fn(),
    netlinkInvite: vi.fn(),
    netlinkApprove: vi.fn(),
    netlinkReject: vi.fn(),
    netlinkRevoke: vi.fn(),
  }
})

const mockGet = vi.mocked(localApi.get)
const mockAvailable = vi.mocked(netlink.netlinkAvailable)
const mockStatus = vi.mocked(netlink.netlinkStatus)

const idle: netlink.NetlinkStatus = {
  hosting: false,
  endpoint_id: null,
  invite: null,
  connected_to: null,
  local_port: null,
  pending: [],
  approved: [],
}

function hosting(extra: Partial<netlink.NetlinkStatus> = {}): netlink.NetlinkStatus {
  return {
    ...idle,
    hosting: true,
    endpoint_id: 'xu4vabcdefghijklmnop7q2m',
    invite: 'trpg:xu4vabcdefghijklmnop7q2m',
    ...extra,
  }
}

async function openNetworkTab() {
  const user = userEvent.setup()
  render(
    <MemoryRouter>
      <SettingsPage />
    </MemoryRouter>,
  )
  await user.click(screen.getByRole('button', { name: '联机' }))
  await screen.findByRole('heading', { name: '内置直连' })
  return user
}

describe('设置页内置直连面板', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockAvailable.mockReturnValue(true)
    mockStatus.mockResolvedValue(idle)
    mockGet.mockImplementation(async (path) => {
      if (path === '/settings/ai/profiles') return []
      if (path === '/net') {
        return {
          lan_enabled: false,
          listening_on_lan: false,
          restart_required: false,
          addresses: [],
          port: 8123,
        }
      }
      if (path === '/settings/ai/quota') return { enabled: false, limit: '100/hour' }
      throw new Error(`unexpected GET ${path}`)
    })
  })

  it('浏览器里打开时说明只有桌面版可用，而不是报错', async () => {
    mockAvailable.mockReturnValue(false)
    await openNetworkTab()
    expect(screen.getByText(/只在桌面版可用/)).toBeInTheDocument()
    expect(netlink.netlinkStatus).not.toHaveBeenCalled()
  })

  it('开启时把后端端口交给隧道——它要反代到那里', async () => {
    const user = await openNetworkTab()
    vi.mocked(netlink.netlinkStart).mockResolvedValue('xu4vabc')
    mockStatus.mockResolvedValue(hosting())

    await user.click(screen.getByRole('switch', { name: '内置直连' }))
    await waitFor(() => expect(netlink.netlinkStart).toHaveBeenCalledWith(8123))
  })

  it('生成邀请码时带上房间码，朋友不用再问一次', async () => {
    mockStatus.mockResolvedValue(hosting())
    const user = await openNetworkTab()
    vi.mocked(netlink.netlinkInvite).mockResolvedValue('trpg:xu4vabc:K7M9PQ2R')

    await user.type(screen.getByLabelText('房间码'), 'k7m9pq2r')
    await user.click(screen.getByRole('button', { name: '生成并复制邀请码' }))

    // 房间码统一大写，和后端的 8 位 base32 对齐。
    await waitFor(() => expect(netlink.netlinkInvite).toHaveBeenCalledWith('K7M9PQ2R'))
    expect(await screen.findByText('trpg:xu4vabc:K7M9PQ2R')).toBeInTheDocument()
  })

  it('有人敲门时列出来，同意后放行', async () => {
    mockStatus.mockResolvedValue(hosting({ pending: ['xu4vstrangerkey9999'] }))
    const user = await openNetworkTab()

    // 公钥太长，界面显示短名
    const short = netlink.shortPeerId('xu4vstrangerkey9999')
    expect(await screen.findByText(short)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: `同意 ${short} 加入` }))
    await waitFor(() =>
      expect(netlink.netlinkApprove).toHaveBeenCalledWith('xu4vstrangerkey9999'),
    )
  })

  it('拒绝不会把人写进名单', async () => {
    mockStatus.mockResolvedValue(hosting({ pending: ['xu4vstrangerkey9999'] }))
    const user = await openNetworkTab()
    const short = netlink.shortPeerId('xu4vstrangerkey9999')

    await user.click(await screen.findByRole('button', { name: `拒绝 ${short}` }))
    await waitFor(() =>
      expect(netlink.netlinkReject).toHaveBeenCalledWith('xu4vstrangerkey9999'),
    )
    expect(netlink.netlinkApprove).not.toHaveBeenCalled()
  })

  it('已批准的朋友可以移出名单', async () => {
    mockStatus.mockResolvedValue(
      hosting({ approved: [{ id: 'peer-a', label: '阿强' }] }),
    )
    const user = await openNetworkTab()

    await user.click(await screen.findByRole('button', { name: '移出 阿强' }))
    await waitFor(() => expect(netlink.netlinkRevoke).toHaveBeenCalledWith('peer-a'))
  })

  it('关着的时候不显示邀请码入口', async () => {
    await openNetworkTab()
    expect(screen.queryByRole('button', { name: '生成并复制邀请码' })).toBeNull()
  })
})
