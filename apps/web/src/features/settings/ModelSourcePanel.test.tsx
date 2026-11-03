import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { localApi } from '@/api/client'
import { ModelSourcePanel } from './ModelSourcePanel'

vi.mock('@/api/client', () => ({
  api: { get: vi.fn() },
  localApi: { get: vi.fn(), put: vi.fn() },
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

describe('ModelSourcePanel', () => {
  beforeEach(() => vi.clearAllMocks())

  it('读取共用的下载源；选 HuggingFace 时显示地址输入框', async () => {
    vi.mocked(localApi.get).mockResolvedValue({ source: 'huggingface', hf_endpoint: 'https://hf-mirror.example.com' })
    render(<ModelSourcePanel />)
    expect(await screen.findByRole('heading', { name: '模型下载源' })).toBeInTheDocument()
    expect(localApi.get).toHaveBeenCalledWith('/settings/model-source')
    expect(screen.getByRole('textbox', { name: 'HuggingFace 地址' })).toHaveValue('https://hf-mirror.example.com')
  })
})
