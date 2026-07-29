import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { localApi } from '@/api/client'
import { SettingsPage } from '@/pages/SettingsPage'

vi.mock('@/api/client', () => ({
  api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  localApi: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}))

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}))

const mockGet = vi.mocked(localApi.get)
const mockPost = vi.mocked(localApi.post)
const mockPut = vi.mocked(localApi.put)

const netStatus = {
  lan_enabled: true,
  listening_on_lan: true,
  restart_required: false,
  addresses: ['192.168.1.20'],
  port: 8123,
}

function renderNetworkSettings() {
  render(
    <MemoryRouter>
      <SettingsPage />
    </MemoryRouter>,
  )
}

async function openNetworkTab(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: '联机' }))
  await screen.findByRole('heading', { name: '允许局域网加入' })
}

describe('设置页联机面板', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGet.mockImplementation(async (path) => {
      if (path === '/settings/ai/profiles') return []
      if (path === '/net') return netStatus
      if (path === '/settings/ai/quota') return { enabled: true, limit: '100/hour' }
      throw new Error(`unexpected GET ${path}`)
    })
  })

  it('展示当前联机状态、可复制地址和 AI 配额', async () => {
    const user = userEvent.setup()
    renderNetworkSettings()
    await openNetworkTab(user)

    expect(await screen.findByRole('switch', { name: '允许局域网加入' })).toBeChecked()
    expect(screen.getByRole('switch', { name: '房间 AI 配额' })).toBeChecked()
    expect(
      screen.getByRole('button', { name: '复制联机地址 http://192.168.1.20:8123' }),
    ).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: '每房间 AI 配额上限' })).toHaveValue('100/hour')
  })

  it('通过开关保存局域网与配额设置', async () => {
    const user = userEvent.setup()
    mockPost.mockResolvedValue({ ...netStatus, lan_enabled: false, addresses: [] })
    mockPut.mockResolvedValue({ enabled: false, limit: '100/hour' })
    renderNetworkSettings()
    await openNetworkTab(user)

    await user.click(await screen.findByRole('switch', { name: '允许局域网加入' }))
    await waitFor(() => expect(mockPost).toHaveBeenCalledWith('/net/lan', { enabled: false }))
    expect(screen.getByRole('switch', { name: '允许局域网加入' })).not.toBeChecked()

    await user.click(screen.getByRole('switch', { name: '房间 AI 配额' }))
    await waitFor(() => expect(mockPut).toHaveBeenCalledWith('/settings/ai/quota', {
      enabled: false,
      limit: '100/hour',
    }))
  })

  it('保存配额后使用后端规范化的值回填输入框', async () => {
    const user = userEvent.setup()
    mockPut.mockResolvedValue({ enabled: true, limit: '100/hour' })
    renderNetworkSettings()
    await openNetworkTab(user)

    const input = await screen.findByRole('textbox', { name: '每房间 AI 配额上限' })
    await user.clear(input)
    await user.type(input, 'not-a-limit{Enter}')

    await waitFor(() => expect(mockPut).toHaveBeenCalledWith('/settings/ai/quota', {
      enabled: true,
      limit: 'not-a-limit',
    }))
    await waitFor(() => expect(input).toHaveValue('100/hour'))
  })

  it('读取失败时给出明确状态并允许重试', async () => {
    const user = userEvent.setup()
    let netAttempts = 0
    mockGet.mockImplementation(async (path) => {
      if (path === '/settings/ai/profiles') return []
      if (path === '/settings/ai/quota') return { enabled: false, limit: '100/hour' }
      if (path === '/net' && netAttempts++ === 0) throw new Error('offline')
      if (path === '/net') return netStatus
      throw new Error(`unexpected GET ${path}`)
    })
    renderNetworkSettings()
    await openNetworkTab(user)

    expect(
      await screen.findByText('读取联机状态失败，当前无法确认是否允许其他设备加入。'),
    ).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '重新读取联机状态' }))

    await waitFor(() => expect(screen.getByRole('switch', { name: '允许局域网加入' })).toBeChecked())
    expect(
      screen.queryByText('读取联机状态失败，当前无法确认是否允许其他设备加入。'),
    ).not.toBeInTheDocument()
  })
})
