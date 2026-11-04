import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { toast } from 'sonner'
import { localApi } from '@/api/client'
import { AgentAdvancedPanel } from './AgentAdvancedPanel'

vi.mock('@/api/client', () => ({
  api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  localApi: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}))

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const mockGet = vi.mocked(localApi.get)
const mockPut = vi.mocked(localApi.put)
const mockDelete = vi.mocked(localApi.delete)

function view(overrides: { calls?: number; fast?: boolean; preset?: string } = {}) {
  const calls = overrides.calls ?? 48
  return {
    context_preset: overrides.preset ?? 'balanced',
    default_context_preset: 'balanced',
    context_presets: [
      { key: 'economy', budget_ceiling: 64000, min_recent_events: 6, max_summary_tokens: 1200, visited_scene_window: 3 },
      { key: 'balanced', budget_ceiling: 150000, min_recent_events: 10, max_summary_tokens: 2000, visited_scene_window: 6 },
      { key: 'full', budget_ceiling: 200000, min_recent_events: 16, max_summary_tokens: 3000, visited_scene_window: 10 },
    ],
    params: [
      {
        key: 'generation_max_calls', label: '每次生成最多模型调用', description: '调用次数上限', kind: 'int',
        value: calls, default: 48, overridden: calls !== 48, minimum: 8, maximum: 200, choices: [], unit: '次',
      },
      {
        key: 'checks_use_fast_model', label: '终检与去口癖先用快模型', description: '先用快模型', kind: 'bool',
        value: overrides.fast ?? true, default: true, overridden: overrides.fast === false,
        minimum: null, maximum: null, choices: [], unit: '',
      },
    ],
  }
}

describe('AgentAdvancedPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGet.mockResolvedValue(view())
  })

  it('显示三档与后端给出的数值，点选档位即保存', async () => {
    mockPut.mockResolvedValue(view({ preset: 'economy' }))
    const user = userEvent.setup()
    render(<AgentAdvancedPanel />)
    const economy = await screen.findByRole('radio', { name: /省钱/ })
    expect(economy).toHaveTextContent('预算上限 64,000')
    expect(screen.getByRole('radio', { name: /均衡/ })).toHaveAttribute('aria-checked', 'true')
    await user.click(economy)
    expect(mockPut).toHaveBeenCalledWith('/settings/agent', { values: {}, context_preset: 'economy' })
    await waitFor(() => expect(screen.getByRole('radio', { name: /省钱/ })).toHaveAttribute('aria-checked', 'true'))
  })

  it('数值项失焦后保存，改过的项显示已修改并可单项恢复默认', async () => {
    mockPut.mockResolvedValue(view({ calls: 64 }))
    mockDelete.mockResolvedValue(view())
    const user = userEvent.setup()
    render(<AgentAdvancedPanel />)
    await user.click(await screen.findByRole('tab', { name: '运行预算' }))
    const input = await screen.findByRole('textbox', { name: '每次生成最多模型调用' })
    await user.clear(input)
    await user.type(input, '64')
    await user.tab()
    expect(mockPut).toHaveBeenCalledWith('/settings/agent', {
      values: { generation_max_calls: 64 }, context_preset: null,
    })
    expect(await screen.findByText('已修改')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '恢复「每次生成最多模型调用」的默认值' }))
    expect(mockDelete).toHaveBeenCalledWith('/settings/agent?key=generation_max_calls')
    await waitFor(() => expect(screen.queryByText('已修改')).not.toBeInTheDocument())
  })

  it('开关项点击即保存；后端拒绝时提示原因并还原输入', async () => {
    mockPut.mockRejectedValueOnce(new Error('每次生成最多模型调用须在 8–200 次之间'))
    const user = userEvent.setup()
    render(<AgentAdvancedPanel />)
    await user.click(await screen.findByRole('tab', { name: '运行预算' }))
    const input = await screen.findByRole('textbox', { name: '每次生成最多模型调用' })
    await user.clear(input)
    await user.type(input, '1')
    await user.tab()
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('每次生成最多模型调用须在 8–200 次之间'))
    expect(input).toHaveValue('48')

    mockPut.mockResolvedValueOnce(view({ fast: false }))
    await user.click(screen.getByRole('switch', { name: '终检与去口癖先用快模型' }))
    expect(mockPut).toHaveBeenLastCalledWith('/settings/agent', {
      values: { checks_use_fast_model: false }, context_preset: null,
    })
  })

  it('上下文档位与运行预算分在两个页签，一次只摊开一页', async () => {
    const user = userEvent.setup()
    render(<AgentAdvancedPanel />)
    expect(await screen.findByRole('radio', { name: /省钱/ })).toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: '每次生成最多模型调用' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: '运行预算' }))
    expect(await screen.findByRole('textbox', { name: '每次生成最多模型调用' })).toBeInTheDocument()
    expect(screen.queryByRole('radio', { name: /省钱/ })).not.toBeInTheDocument()
  })

  it('全部恢复默认需要二次确认', async () => {
    mockDelete.mockResolvedValue(view())
    const user = userEvent.setup()
    render(<AgentAdvancedPanel />)
    await user.click(await screen.findByRole('tab', { name: '运行预算' }))
    await user.click(await screen.findByRole('button', { name: '全部恢复默认' }))
    expect(mockDelete).not.toHaveBeenCalled()
    await user.click(await screen.findByRole('button', { name: '恢复默认' }))
    expect(mockDelete).toHaveBeenCalledWith('/settings/agent')
  })
})
