import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useState } from 'react'
import { localApi } from '@/api/client'
import { toast } from 'sonner'
import { ModelNameField } from './ModelNameField'

vi.mock('@/api/client', () => ({
  api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  localApi: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const mockPost = vi.mocked(localApi.post)

/** 受控组件，得有人替它拿着值——照实模拟表单那一侧。 */
function Harness({ initial = '' }: { initial?: string }) {
  const [value, setValue] = useState(initial)
  return (
    <ModelNameField
      protocol="openai"
      baseUrl="https://api.deepseek.com"
      value={value}
      placeholder="deepseek-chat"
      onChange={setValue}
      resolveKey={async () => 'sk-real'}
    />
  )
}

const THREE = ['deepseek-v4-flash', 'deepseek-v4-flash-vision-exp', 'deepseek-v4-pro']

function upstreamReturns(models: string[]) {
  mockPost.mockResolvedValue({ success: true, models, message: `找到 ${models.length} 个模型` })
}

async function clickFetch(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: /获取可用模型/ }))
}

describe('模型名称：问上游要清单', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('拉完就把候选摊开，不用再去找哪儿能点', async () => {
    const user = userEvent.setup()
    upstreamReturns(THREE)
    render(<Harness />)

    await clickFetch(user)

    const options = await screen.findAllByRole('option')
    expect(options.map((o) => o.textContent)).toEqual(THREE)
  })

  it('输入框里已经有模型名时，候选照样是全部', async () => {
    // 这条是踩出来的：一进编辑态框里就填着当前配置的模型名，若拿它去过滤，
    // 一屏候选只会剩它自己——拉了等于白拉。
    const user = userEvent.setup()
    upstreamReturns(THREE)
    render(<Harness initial="deepseek-v4-pro" />)

    await clickFetch(user)

    expect((await screen.findAllByRole('option')).length).toBe(3)
  })

  it('自己动手打字之后才按内容过滤', async () => {
    const user = userEvent.setup()
    upstreamReturns(THREE)
    render(<Harness />)
    await clickFetch(user)

    await user.type(screen.getByRole('combobox'), 'vision')

    await waitFor(() => {
      const options = screen.getAllByRole('option')
      expect(options.map((o) => o.textContent)).toEqual(['deepseek-v4-flash-vision-exp'])
    })
    expect(screen.getByText(/3 个里有 1 个含「vision」/)).toBeInTheDocument()
  })

  it('点一个候选就填进去并收起', async () => {
    const user = userEvent.setup()
    upstreamReturns(THREE)
    render(<Harness />)
    await clickFetch(user)

    await user.click(await screen.findByRole('option', { name: 'deepseek-v4-pro' }))

    expect(screen.getByRole('combobox')).toHaveValue('deepseek-v4-pro')
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  })

  it('选完再点开，仍然看得到全部候选', async () => {
    const user = userEvent.setup()
    upstreamReturns(THREE)
    render(<Harness />)
    await clickFetch(user)
    await user.click(await screen.findByRole('option', { name: 'deepseek-v4-pro' }))

    await user.click(screen.getByRole('button', { name: '展开模型列表' }))

    expect((await screen.findAllByRole('option')).length).toBe(3)
  })

  it('拿真实密钥去问，而不是表单里那串掩码', async () => {
    const user = userEvent.setup()
    upstreamReturns(THREE)
    render(<Harness />)

    await clickFetch(user)

    expect(mockPost).toHaveBeenCalledWith('/settings/ai/models', {
      protocol: 'openai',
      base_url: 'https://api.deepseek.com',
      api_key: 'sk-real',
    })
  })

  it('上游没有这个接口时照转它的说法，不摆出空下拉', async () => {
    const user = userEvent.setup()
    mockPost.mockResolvedValue({
      success: false, models: [], message: '这个服务没有提供模型清单接口，模型名请手动填写。',
    })
    render(<Harness />)

    await clickFetch(user)

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith('这个服务没有提供模型清单接口，模型名请手动填写。')
    })
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '展开模型列表' })).not.toBeInTheDocument()
  })

  it('没拉过清单时就是个普通输入框，手填这条路不堵', async () => {
    const user = userEvent.setup()
    render(<Harness />)

    await user.type(screen.getByRole('textbox'), 'qwen3.7-plus')

    expect(screen.getByRole('textbox')).toHaveValue('qwen3.7-plus')
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  })
})
