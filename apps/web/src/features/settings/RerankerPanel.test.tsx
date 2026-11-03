import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { localApi } from '@/api/client'
import { RerankerPanel } from './RerankerPanel'

vi.mock('@/api/client', () => ({
  api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  localApi: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const mockGet = vi.mocked(localApi.get)

function view(model: Record<string, unknown> = {}, enabled = false) {
  return {
    enabled,
    models: [{
      id: 'bge-reranker-v2-m3-int8', label: 'bge-reranker-v2-m3（int8）', description: '重排', dim: 0,
      size_bytes: 587_812_000, recommended: true, index_speed: '每次查阅约多 2～4 秒', memory: '约 1GB',
      installed: false, active: false, target: false, download: null, ...model,
    }],
  }
}

const routeGet = (v: ReturnType<typeof view>) =>
  mockGet.mockImplementation((url: string) => Promise.resolve(url === '/tasks' ? [] : v) as never)

describe('RerankerPanel', () => {
  beforeEach(() => vi.clearAllMocks())

  it('独立的精排卡：未下载时开关不可用，点下载请求精排自己的接口', async () => {
    routeGet(view())
    vi.mocked(localApi.post).mockResolvedValue(view({ download: { state: 'downloading', done_bytes: 1e8, total_bytes: 587_812_000, error: '' } }))
    render(<RerankerPanel />)
    expect(await screen.findByRole('heading', { name: '精排模型' })).toBeInTheDocument()
    expect(screen.getByRole('switch', { name: 'KP 查阅模组原文时精排' })).toBeDisabled()
    expect(screen.getByText(/下载 588 MB/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /下载 bge-reranker/ }))
    expect(localApi.post).toHaveBeenCalledWith('/settings/reranker/models/bge-reranker-v2-m3-int8/download')
    expect(await screen.findByRole('progressbar', { name: /下载进度/ })).toBeInTheDocument()
    expect(mockGet).toHaveBeenCalledWith('/tasks')   // 下载开始后「解析进度」立即去拉任务
  })

  it('已下载后开关发请求', async () => {
    routeGet(view({ installed: true }))
    vi.mocked(localApi.put).mockResolvedValue(view({ installed: true, active: true }, true))
    render(<RerankerPanel />)
    const sw = await screen.findByRole('switch', { name: 'KP 查阅模组原文时精排' })
    await userEvent.click(sw)
    expect(localApi.put).toHaveBeenCalledWith('/settings/reranker', { enabled: true })
    expect(await screen.findByRole('switch', { name: 'KP 查阅模组原文时精排' })).toHaveAttribute('aria-checked', 'true')
  })
})
