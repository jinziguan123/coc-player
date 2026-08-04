import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { localApi } from '@/api/client'
import { AISettingsPanel } from './AISettingsPanel'

vi.mock('@/api/client', () => ({
  api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  localApi: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}))

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

/** 生图配置在第二个 tab 里，Radix 只渲染选中页，测它得先切过去。 */
async function openImageTab(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('tab', { name: '生图模型' }))
}

/** Modal 是无 role 的 portal 容器，用弹窗标题回溯到面板本身作为查询范围。 */
async function findDialog(title: string | RegExp): Promise<HTMLElement> {
  const heading = await screen.findByRole('heading', { name: title })
  return heading.closest('.modal-panel') as HTMLElement
}

const mockGet = vi.mocked(localApi.get)
const mockPost = vi.mocked(localApi.post)
const mockPut = vi.mocked(localApi.put)

const chatProfile = {
  id: 'c1',
  name: 'deepseek 主力',
  protocol: 'openai' as const,
  base_url: 'https://api.deepseek.com',
  model_name: 'deepseek-chat',
  api_key: 'sk-1****4321',
  is_active: true,
  vision: false,
  context_window: 0,
  reasoning_effort: '',
}

const claudeProfile = {
  ...chatProfile,
  id: 'c2',
  name: 'claude',
  protocol: 'anthropic' as const,
  model_name: 'claude-sonnet-4',
  is_active: false,
  reasoning_effort: 'high', // 历史遗留值：Anthropic 下不会下发
}

const imageProfile = {
  id: 'i1',
  name: 'ComfyUI（172.30.18.236）',
  backend: 'comfyui' as const,
  is_active: true,
  model: '',
  base_url: '',
  api_key: '',
  comfyui_base_url: 'http://172.30.18.236:8188',
  comfyui_workflow: '',
}

function mockLists(chat: unknown[], image: unknown[]) {
  mockGet.mockImplementation(async (path: string) => {
    if (path === '/settings/ai/profiles') return chat
    if (path === '/settings/ai/image-profiles') return image
    throw new Error(`unexpected GET ${path}`)
  })
}

describe('AI 配置面板', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockLists([chatProfile], [imageProfile])
  })

  it('对话模型与生图模型各自成页，一次只显示一套配置', async () => {
    const user = userEvent.setup()
    render(<AISettingsPanel />)

    // 默认停在「对话模型」：只看得到对话配置
    expect(await screen.findByRole('tab', { name: '对话模型' })).toHaveAttribute(
      'data-state', 'active',
    )
    expect(screen.getByRole('button', { name: '编辑 deepseek 主力' })).toBeInTheDocument()
    expect(screen.queryByText('ComfyUI（172.30.18.236）')).not.toBeInTheDocument()

    await openImageTab(user)
    expect(await screen.findByText('ComfyUI（172.30.18.236）')).toBeInTheDocument()
    expect(screen.getByText('http://172.30.18.236:8188')).toBeInTheDocument()
    // 对话配置整套都退场了，不是叠在下面
    expect(screen.queryByRole('button', { name: '编辑 deepseek 主力' })).not.toBeInTheDocument()
  })

  it('没有生图配置时明确说明「不出图但不影响跑团」', async () => {
    const user = userEvent.setup()
    mockLists([chatProfile], [])
    render(<AISettingsPanel />)
    await openImageTab(user)

    expect(await screen.findByText(/还没有添加生图模型/)).toBeInTheDocument()
  })

  it('编辑弹窗把连接四件套收在同一页，密钥不再被高级配置隔开', async () => {
    const user = userEvent.setup()
    render(<AISettingsPanel />)
    await user.click(await screen.findByRole('button', { name: '编辑 deepseek 主力' }))

    const dialog = await findDialog('编辑配置')
    // 「连接」是默认页：名称/协议/地址/模型/密钥同屏可见
    expect(within(dialog).getByDisplayValue('deepseek 主力')).toBeInTheDocument()
    expect(within(dialog).getByDisplayValue('https://api.deepseek.com')).toBeInTheDocument()
    expect(within(dialog).getByDisplayValue('deepseek-chat')).toBeInTheDocument()
    expect(within(dialog).getByDisplayValue('sk-1****4321')).toBeInTheDocument()
    // 生图字段已经不在对话配置里了（连同整个生图页都是独立的）
    expect(within(dialog).queryByText(/ComfyUI/)).not.toBeInTheDocument()
    expect(within(dialog).queryByText(/生图/)).not.toBeInTheDocument()
  })

  it('思考等级由用户手填，任意取值都原样保存', async () => {
    const user = userEvent.setup()
    mockPut.mockResolvedValue(chatProfile)
    render(<AISettingsPanel />)
    await user.click(await screen.findByRole('button', { name: '编辑 deepseek 主力' }))

    const dialog = await findDialog('编辑配置')
    await user.click(within(dialog).getByRole('tab', { name: '能力' }))

    // 是输入框而非下拉：各家取值不统一，写死选项会把能用的值挡在外面
    const field = within(dialog).getByPlaceholderText(/可填 low \/ high \/ max/)
    expect(field.tagName).toBe('INPUT')
    await user.type(field, 'ultra')

    await user.click(within(dialog).getByRole('button', { name: '保存' }))
    await waitFor(() => expect(mockPut).toHaveBeenCalled())
    expect(mockPut.mock.calls[0][1]).toMatchObject({ reasoning_effort: 'ultra' })
  })

  it('「关闭模型思考」勾上后随表单保存，并禁用只调强度的思考等级', async () => {
    // 思考默认是开的、effort 默认 high，这个开关是唯一能真正关掉它的途径；
    // 而 reasoning_effort 只调强度、关不掉思考，两者同时可填只会让人以为自己关了。
    const user = userEvent.setup()
    mockPut.mockResolvedValue(chatProfile)
    render(<AISettingsPanel />)
    await user.click(await screen.findByRole('button', { name: '编辑 deepseek 主力' }))

    const dialog = await findDialog('编辑配置')
    await user.click(within(dialog).getByRole('tab', { name: '能力' }))

    const effort = within(dialog).getByPlaceholderText(/可填 low \/ high \/ max/)
    expect(effort).not.toBeDisabled()

    await user.click(within(dialog).getByRole('checkbox', { name: /关闭模型思考/ }))
    expect(effort).toBeDisabled()          // 关了思考，强度就没有意义了

    await user.click(within(dialog).getByRole('button', { name: '保存' }))
    await waitFor(() => expect(mockPut).toHaveBeenCalled())
    expect(mockPut.mock.calls[0][1]).toMatchObject({ thinking_disabled: true })
  })

  it('Anthropic 配置不显示思考等级，但残留值会被点名并可一键清除', async () => {
    const user = userEvent.setup()
    mockLists([claudeProfile], [imageProfile])
    mockPut.mockResolvedValue(claudeProfile)
    render(<AISettingsPanel />)
    await user.click(await screen.findByRole('button', { name: '编辑 claude' }))

    const dialog = await findDialog('编辑配置')
    await user.click(within(dialog).getByRole('tab', { name: '能力' }))

    // 输入框不出现——填了也不会下发，摆着只会误导
    expect(within(dialog).queryByPlaceholderText(/如需指定填/)).not.toBeInTheDocument()
    // 但旧值必须说清楚它已经失效，否则用户以为还在起作用
    expect(within(dialog).getByRole('alert')).toHaveTextContent(/不支持这项设置/)

    await user.click(within(dialog).getByRole('button', { name: '清除' }))
    expect(within(dialog).queryByRole('alert')).not.toBeInTheDocument()

    await user.click(within(dialog).getByRole('button', { name: '保存' }))
    await waitFor(() => expect(mockPut).toHaveBeenCalled())
    expect(mockPut.mock.calls[0][1]).toMatchObject({ reasoning_effort: '' })
  })

  it('保存对话配置时不再夹带任何生图字段', async () => {
    const user = userEvent.setup()
    mockPut.mockResolvedValue(chatProfile)
    render(<AISettingsPanel />)
    await user.click(await screen.findByRole('button', { name: '编辑 deepseek 主力' }))

    const dialog = await findDialog('编辑配置')
    await user.click(within(dialog).getByRole('button', { name: '保存' }))
    await waitFor(() => expect(mockPut).toHaveBeenCalled())

    const body = mockPut.mock.calls[0][1] as Record<string, unknown>
    for (const key of [
      'image_model', 'image_backend', 'image_base_url', 'image_api_key',
      'comfyui_base_url', 'comfyui_workflow',
    ]) {
      expect(body).not.toHaveProperty(key)
    }
  })

  it('生图配置走自己的端点增删改与激活', async () => {
    const user = userEvent.setup()
    mockLists([chatProfile], [{ ...imageProfile, is_active: false }])
    mockPost.mockResolvedValue({ status: 'ok' })
    render(<AISettingsPanel />)
    await openImageTab(user)

    await user.click(await screen.findByRole('button', { name: /使用 ComfyUI（172.30.18.236） 出图/ }))
    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith('/settings/ai/image-profiles/i1/activate'),
    )

    await user.click(screen.getByRole('button', { name: /测试生图 ComfyUI/ }))
    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith('/settings/ai/image-profiles/i1/test'),
    )
  })

  it('新增生图配置时按后端切换表单字段', async () => {
    const user = userEvent.setup()
    render(<AISettingsPanel />)
    await openImageTab(user)
    await user.click(await screen.findByRole('button', { name: '+ 新增配置' }))

    const dialog = await findDialog('新增生图配置')
    // 默认 OpenAI 后端：填模型名与密钥，不该出现 ComfyUI 工作流框
    expect(within(dialog).getByPlaceholderText(/dall-e-3/)).toBeInTheDocument()
    expect(within(dialog).queryByPlaceholderText(/PLACEHOLDER_POSITIVE/)).not.toBeInTheDocument()
  })
})
