import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { localApi } from '@/api/client'
import { EmbeddingModelPanel } from './EmbeddingModelPanel'

vi.mock('@/api/client', () => ({
  api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  localApi: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}))

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const mockGet = vi.mocked(localApi.get)
const mockPost = vi.mocked(localApi.post)

type Over = {
  large?: Record<string, unknown>; rebuild?: Record<string, unknown>; target?: string | null; stale?: number
  reranker?: Record<string, unknown>; rerankerEnabled?: boolean
}

function view(o: Over = {}) {
  const base = { dim: 1024, recommended: false, index_speed: '约 5 块/秒', memory: '约 2.6GB', installed: false, active: false, target: false, download: null }
  return {
    active: 'bge-small-zh-v1.5',
    target: o.target ?? null,
    source: 'modelscope',
    hf_endpoint: '',
    stale: o.stale ?? 0,
    rebuild: { running: false, target: '', done: 0, total: 0, current: '', error: '', ...o.rebuild },
    models: [
      { ...base, id: 'bge-small-zh-v1.5', label: 'bge-small-zh-v1.5（默认）', description: '轻量', dim: 512, size_bytes: 95_221_432, installed: true, active: true },
      { ...base, id: 'bge-large-zh-v1.5-int8', label: 'bge-large-zh-v1.5（int8，推荐）', description: '效果最好', size_bytes: 326_604_607, recommended: true, ...o.large },
    ],
    rerankers: [
      { ...base, id: 'bge-reranker-v2-m3-int8', label: 'bge-reranker-v2-m3（int8）', description: '重排', dim: 0,
        size_bytes: 587_812_000, index_speed: '每次查阅约多 2～4 秒', memory: '约 1GB', ...o.reranker },
    ],
    reranker_enabled: o.rerankerEnabled ?? false,
  }
}

describe('EmbeddingModelPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGet.mockResolvedValue(view())
  })

  it('显示大小、使用中与推荐，点下载即请求下载', async () => {
    mockPost.mockResolvedValue(view({ large: { download: { state: 'downloading', done_bytes: 1e8, total_bytes: 326_604_607, error: '' } } }))
    render(<EmbeddingModelPanel />)
    expect(await screen.findByText('bge-large-zh-v1.5（int8，推荐）')).toBeInTheDocument()
    expect(screen.getByText(/下载 327 MB/)).toBeInTheDocument()
    expect(screen.getByText('使用中')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /下载 bge-large/ }))
    expect(mockPost).toHaveBeenCalledWith('/settings/embedding/models/bge-large-zh-v1.5-int8/download')
    expect(await screen.findByRole('progressbar', { name: /下载进度/ })).toHaveAttribute('aria-valuenow', '31')
    expect(screen.getByRole('button', { name: /取消/ })).toBeInTheDocument()
  })

  it('已下载的模型可切换（二次确认），确认后请求切换', async () => {
    mockGet.mockResolvedValue(view({ large: { installed: true } }))
    mockPost.mockResolvedValue(view({ large: { installed: true, target: true }, target: 'bge-large-zh-v1.5-int8', rebuild: { running: true, done: 10, total: 40, current: '规则书《手册》' } }))
    render(<EmbeddingModelPanel />)
    await userEvent.click(await screen.findByRole('button', { name: /切换/ }))
    expect(screen.getByRole('dialog')).toHaveTextContent(/重建期间检索照常可用/)
    await userEvent.click(screen.getByRole('button', { name: '开始切换' }))
    expect(mockPost).toHaveBeenCalledWith('/settings/embedding/switch', { model_id: 'bge-large-zh-v1.5-int8' })
    expect(await screen.findByText(/正在重建索引/)).toBeInTheDocument()
    expect(screen.getByText(/规则书《手册》 · 10 \/ 40 条/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /取消切换/ })).toBeInTheDocument()
  })

  it('下载失败显示原因并可重试；有旧索引时提示重建', async () => {
    mockGet.mockResolvedValue(view({ stale: 12, large: { download: { state: 'failed', done_bytes: 0, total_bytes: 1, error: '连接超时，请检查网络或更换下载源' } } }))
    render(<EmbeddingModelPanel />)
    expect(await screen.findByText(/下载失败：连接超时/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /重试下载/ })).toBeInTheDocument()
    expect(screen.getByText('有 12 条旧索引待重建')).toBeInTheDocument()
  })

  it('精排：未下载时开关不可用，可下载；已下载后开关发请求', async () => {
    const { unmount } = render(<EmbeddingModelPanel />)
    const sw = await screen.findByRole('switch', { name: 'KP 查阅模组原文时精排' })
    expect(sw).toBeDisabled()
    expect(screen.getByText(/需先下载精排模型/)).toBeInTheDocument()
    expect(screen.getByText(/下载 588 MB/)).toBeInTheDocument()
    unmount()

    mockGet.mockResolvedValue(view({ reranker: { installed: true } }))
    vi.mocked(localApi.put).mockResolvedValue(view({ reranker: { installed: true, active: true }, rerankerEnabled: true }))
    render(<EmbeddingModelPanel />)
    const enabled = await screen.findByRole('switch', { name: 'KP 查阅模组原文时精排' })
    expect(enabled).not.toBeDisabled()
    await userEvent.click(enabled)
    expect(localApi.put).toHaveBeenCalledWith('/settings/embedding/reranker', { enabled: true })
    expect(await screen.findByRole('switch', { name: 'KP 查阅模组原文时精排' })).toHaveAttribute('aria-checked', 'true')
  })
})
