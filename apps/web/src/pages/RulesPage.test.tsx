import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { RulesPage } from './RulesPage'
import MANIFEST from '@/components/game/__fixtures__/coc-manifest.json'
import { canLeave } from '@/lib/navGuard'

const get = vi.fn()
const put = vi.fn()
const post = vi.fn()
let serverUrl = ''
vi.mock('../api/client', () => ({
  api: {
    get: (...a: unknown[]) => get(...a),
    put: (...a: unknown[]) => put(...a),
    post: (...a: unknown[]) => post(...a),
  },
  localApi: { delete: vi.fn() },
  getServerUrl: () => serverUrl,
  getApiBase: () => '/api',
  getPlayerToken: () => 't',
}))
const toastError = vi.fn()
vi.mock('sonner', () => ({ toast: { success: vi.fn(), warning: vi.fn(), error: (...a: unknown[]) => toastError(...a) } }))

// 规则书原文（schema 默认值）
const RAW: Record<string, unknown> = {}
for (const c of MANIFEST.components) {
  const props = (c.config_schema as { properties?: Record<string, { default?: unknown }> } | null)?.properties ?? {}
  for (const [k, p] of Object.entries(props)) RAW[k] = p.default
}

const SKILLS = { table: 'skills', rows: [{ name: '会计', base: 5 }, { name: '聆听', base: 20 }],
  status: { 会计: 'official', 聆听: 'official' }, deleted: [], originals: {}, patches: [] }

let village: Record<string, unknown>
beforeEach(() => {
  get.mockReset(); put.mockReset(); post.mockReset(); toastError.mockReset(); serverUrl = ''
  village = { rule_system: 'coc', options: {}, effective: { ...RAW }, table_notes: '', enabled: true, labels: [], data: {} }
  get.mockImplementation((path: string) => {
    if (path.endsWith('/manifest')) return Promise.resolve(MANIFEST)
    if (path.includes('/data/')) return Promise.resolve(SKILLS)
    if (path.startsWith('/rulebooks/plugins')) return Promise.resolve({ directory: '/p', api_version: 1, plugins: [] })
    if (path === '/rulebooks') return Promise.resolve([])
    return Promise.resolve(village)
  })
  put.mockImplementation((_p: string, body: { options: Record<string, unknown> }) =>
    Promise.resolve({ ...village, effective: body.options }))
})

async function open(url = '/rules') {
  const user = userEvent.setup()
  render(<MemoryRouter initialEntries={[url]}><RulesPage /></MemoryRouter>)
  await screen.findByRole('region', { name: '检定' })
  return user
}

const draftBar = () => screen.queryByRole('region', { name: '未保存的改动' })

describe('规则页', () => {
  it('分区导航与各规则分区来自 manifest', async () => {
    await open()
    const rail = screen.getByRole('navigation', { name: '规则分区' })
    for (const t of ['概览', '检定', '幸运', '伤害与状态', '理智与疯狂', '成长', '数据表', '桌面约定', '插件']) {
      expect(within(rail).getByRole('button', { name: t })).toBeInTheDocument()
    }
  })

  it('改一项：草稿条写明改了什么，放弃后回到已保存的样子', async () => {
    const user = await open()
    expect(draftBar()).toBeNull()
    await user.click(screen.getByRole('radio', { name: '只认 100' }))
    expect(within(draftBar()!).getByText('大失败阈值 照规则书→只认 100')).toBeInTheDocument()
    expect(screen.getByText(/原文/, { selector: '.rules-was' })).toBeInTheDocument()

    await user.click(within(draftBar()!).getByRole('button', { name: '放弃' }))
    await waitFor(() => expect(draftBar()).toBeNull())
    expect(screen.getByRole('radio', { name: '照规则书' })).toHaveAttribute('aria-checked', 'true')
  })

  it('「恢复原文」把这一项改回规则书的值', async () => {
    village = { ...village, options: { critical_max: 3 }, effective: { ...RAW, critical_max: 3 } }
    const user = await open()
    const row = document.getElementById('rule-critical_max')!
    await user.click(within(row).getByTitle('恢复规则书原文'))
    expect(within(row).getByRole('spinbutton', { name: '大成功阈值' })).toHaveValue(1)
    expect(within(draftBar()!).getByText('大成功阈值 3→1')).toBeInTheDocument()
  })

  it('可选规则：打开开关才展开它的参数', async () => {
    const user = await open()
    expect(screen.queryByRole('spinbutton', { name: '每轮失血' })).toBeNull()
    await user.click(screen.getByRole('switch', { name: '流血' }))
    expect(await screen.findByRole('spinbutton', { name: '每轮失血' })).toBeInTheDocument()
    expect(within(draftBar()!).getByText('流血 开')).toBeInTheDocument()
  })

  it('「只看改过的」收起与原文相同的项', async () => {
    village = { ...village, effective: { ...RAW, critical_max: 3 } }
    const user = await open()
    await user.click(screen.getByRole('switch', { name: '只看改过的' }))
    expect(screen.getByText('大成功阈值', { selector: '.rules-row-label' })).toBeInTheDocument()
    expect(screen.queryByText('奖惩骰上限', { selector: '.rules-row-label' })).toBeNull()
  })

  it('一次保存：参数、约定、开关与改过的数据表一起提交', async () => {
    const user = await open()
    await user.click(screen.getByRole('radio', { name: '只认 100' }))
    await user.type(screen.getByRole('textbox', { name: '桌面约定' }), '重调查')
    // 改一张数据表
    await user.click(within(screen.getByRole('navigation', { name: '规则分区' })).getByRole('button', { name: '数据表' }))
    await user.click(await screen.findByText('会计'))
    const base = screen.getByLabelText('起始值')
    await user.clear(base)
    await user.type(base, '9')
    await user.tab()

    await user.click(within(draftBar()!).getByRole('button', { name: '保存改动' }))
    await waitFor(() => expect(put).toHaveBeenCalled())
    const [path, body] = put.mock.calls[0] as [string, Record<string, unknown>]
    expect(path).toBe('/rulebooks/village-rules/coc')
    expect(body).toMatchObject({ table_notes: '重调查', enabled: true })
    expect((body.options as Record<string, unknown>).fumble_rule).toBe('hundred_only')
    expect((body.tables as Record<string, Array<Record<string, unknown>>>).skills[0]).toEqual({ name: '会计', base: 9 })
  })

  it('保存失败：草稿原样留着，错误逐条报出', async () => {
    put.mockRejectedValue(new Error('「乙」与「甲」不能同时开启\n武器表·第 3 行（坏枪）伤害：必填'))
    const user = await open()
    await user.click(screen.getByRole('radio', { name: '只认 100' }))
    await user.click(within(draftBar()!).getByRole('button', { name: '保存改动' }))
    await waitFor(() => expect(toastError).toHaveBeenCalled())
    expect(toastError.mock.calls[0][0]).toBe('「乙」与「甲」不能同时开启')
    expect(draftBar()).not.toBeNull()
  })

  it('导入房规只载入草稿，不直接保存', async () => {
    post.mockResolvedValue({
      ok: true, name: '老张的桌', description: '', options: { critical_max: 5 }, table_notes: '重调查',
      data: {}, tables: { skills: [{ name: '会计', base: 5 }, { name: '聆听', base: 20 }, { name: '无线电', base: 10 }] },
      labels: ['大成功 ≤ 5', '技能表：新增 1'], errors: [], warnings: [],
    })
    const user = await open()
    await user.click(screen.getByRole('button', { name: /导入房规/ }))
    const file = new File(['format: coc-player/house-rules'], 'rules.yaml', { type: 'text/yaml' })
    await user.upload(screen.getByLabelText('选择房规文件'), file)
    await user.click(await screen.findByRole('button', { name: '载入到草稿' }))

    await waitFor(() => expect(draftBar()).not.toBeNull())
    const bar = within(draftBar()!)
    expect(bar.getByText('大成功阈值 1→5')).toBeInTheDocument()
    expect(bar.getByText('技能表 +无线电')).toBeInTheDocument()
    expect(bar.getByText('桌面约定')).toBeInTheDocument()
    expect(put).not.toHaveBeenCalled()
  })

  it('有未保存改动时，侧栏跳转被拦下并列出改动', async () => {
    const user = await open()
    expect(canLeave('/characters')).toBe(true)
    await user.click(screen.getByRole('radio', { name: '只认 100' }))
    let allowed = true
    act(() => { allowed = canLeave('/characters') })
    expect(allowed).toBe(false)
    expect(await screen.findByText('还有 1 处改动没保存')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '继续编辑' }))
    expect(draftBar()).not.toBeNull()
  })
})


describe('本桌规则与规则书两个标签', () => {
  it('标签上写明各自的职责', async () => {
    await open()
    expect(screen.getByRole('tab', { name: /本桌规则.*决定骰子怎么判/ })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: /规则书.*给 AI 守秘人查阅的参考/ })).toBeInTheDocument()
    expect(screen.getByText(/按这里结算/)).toBeInTheDocument()
  })

  it('展开对照表：逐项说明影响什么、不影响什么、冲突时以谁为准', async () => {
    const user = await open()
    await user.click(screen.getByRole('button', { name: '两者有什么区别？' }))
    const table = await screen.findByRole('region', { name: '本桌规则与规则书的区别' })
    expect(within(table).getByText('不影响什么')).toBeInTheDocument()
    expect(within(table).getByText(/任何一次掷骰结果/)).toBeInTheDocument()
    expect(within(table).getByText(/两者冲突时以本桌规则为准/)).toBeInTheDocument()
  })

  it('规则书标签：说明它不改判定；切走再切回来，没保存的改动还在', async () => {
    const user = await open()
    await user.click(screen.getByRole('radio', { name: '只认 100' }))
    await user.click(screen.getByRole('tab', { name: /规则书/ }))
    expect(await screen.findByText(/不会改变任何一次掷骰结果/)).toBeInTheDocument()
    expect(screen.getByText('尚无规则书')).toBeInTheDocument()
    expect(draftBar()).not.toBeNull()          // 草稿条两个标签下都在
    await user.click(screen.getByRole('tab', { name: /本桌规则/ }))
    expect(await screen.findByRole('radio', { name: '只认 100' })).toHaveAttribute('aria-checked', 'true')
  })

  it('地址里的 tab=books 直接打开规则书标签', async () => {
    render(<MemoryRouter initialEntries={['/rules?tab=books']}><RulesPage /></MemoryRouter>)
    expect(await screen.findByRole('tab', { name: /规则书/ })).toHaveAttribute('aria-selected', 'true')
  })
})


describe('联机客人', () => {
  it('能看不能改：说明只有房主能改，控件禁用，没有导入与总开关', async () => {
    serverUrl = 'http://192.168.1.5:8000'
    await open()
    expect(screen.getByRole('note')).toHaveTextContent('只有房主能修改')
    expect(screen.getByRole('radio', { name: '只认 100' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: /导入房规/ })).toBeNull()
    expect(screen.queryByRole('switch', { name: '启用本桌规则' })).toBeNull()
  })
})
