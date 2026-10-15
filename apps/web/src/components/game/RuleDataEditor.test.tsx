import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { RuleDataEditor } from './RuleDataEditor'
import MANIFEST from './__fixtures__/coc-manifest.json'

const get = vi.fn()
const put = vi.fn()
vi.mock('../../api/client', () => ({
  api: {
    get: (...a: unknown[]) => get(...a),
    put: (...a: unknown[]) => put(...a),
  },
}))
const warning = vi.fn()
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: (...a: unknown[]) => warning(...a) } }))

const BOW = { name: '弓箭', skill: '射击(弓弩)', dam: '1D6+半DB', tho: 0, range: '30', round: '1', num: '1', price: '', err: '97', time: '', category: '常规' }
const AXE = { name: '斧头', skill: '格斗(斧)', dam: '1D8+DB', tho: 0, range: '接触', round: '1', num: '', price: '', err: '', time: '', category: '常规' }

function view(overrides: Record<string, unknown> = {}) {
  return {
    table: 'skills', rows: [{ name: '会计', base: 5 }, { name: '聆听', base: 20 }],
    status: { 会计: 'official', 聆听: 'official' }, deleted: [], originals: {}, patches: [],
    ...overrides,
  }
}

let weaponsView: Record<string, unknown>
beforeEach(() => {
  get.mockReset()
  put.mockReset()
  warning.mockReset()
  weaponsView = {
    table: 'weapons', rows: [BOW, AXE], status: { 弓箭: 'official', 斧头: 'official' },
    deleted: [], originals: {}, patches: [],
  }
  get.mockImplementation((path: string) => {
    if (path.endsWith('/manifest')) return Promise.resolve(MANIFEST)
    if (path === '/rulebooks/weapon-holders/coc') return Promise.resolve({ holders: { 斧头: 3 } })
    if (path.endsWith('/weapons')) return Promise.resolve(weaponsView)
    return Promise.resolve(view())
  })
  put.mockImplementation((_path: string, body: { rows: unknown[] }) =>
    Promise.resolve({ ...weaponsView, rows: body.rows }))
})

async function openWeapons() {
  const user = userEvent.setup()
  render(<RuleDataEditor scope={{ ruleSystem: 'coc', moduleId: 'm1' }} />)
  await user.click(await screen.findByRole('tab', { name: '武器表' }))
  await screen.findByText('弓箭')
  return user
}

describe('数据表编辑器', () => {
  it('表的页签来自 manifest，读写模组自己的接口', async () => {
    await openWeapons()
    for (const t of ['技能表', '职业表', '专精表', '装备表', '武器表', '疯狂症状表']) {
      expect(screen.getByRole('tab', { name: t })).toBeInTheDocument()
    }
    expect(get).toHaveBeenCalledWith('/modules/m1/rule-data/weapons')
  })

  it('改一行后标「已修改」，保存提交整张表', async () => {
    const user = await openWeapons()
    await user.click(screen.getByText('弓箭'))
    const dam = screen.getByLabelText('伤害')
    await user.clear(dam)
    await user.type(dam, '1D8')
    expect(within(screen.getAllByRole('row')[1]).getByText('已修改')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /保存武器表/ }))
    await waitFor(() => expect(put).toHaveBeenCalled())
    const [path, body] = put.mock.calls[0] as [string, { rows: Array<Record<string, unknown>> }]
    expect(path).toBe('/modules/m1/rule-data/weapons')
    expect(body.rows.find((r) => r.name === '弓箭')!.dam).toBe('1D8')
    expect(body.rows).toHaveLength(2)
  })

  it('已有行的主键锁住，不能悄悄改名', async () => {
    const user = await openWeapons()
    await user.click(screen.getByText('斧头'))
    expect(screen.getByLabelText('名称')).toBeDisabled()
  })

  it('删除官方行进「已删除」区，可以恢复', async () => {
    const user = await openWeapons()
    await user.click(screen.getByText('斧头'))
    await user.click(screen.getByRole('button', { name: /删除/ }))
    expect(screen.getByText(/已删除的官方行（1）/)).toBeInTheDocument()
    await user.click(screen.getByTitle('恢复这一行'))
    expect(screen.queryByText(/已删除的官方行/)).not.toBeInTheDocument()
  })

  it('删除有角色持有的武器时提醒人数，没人持有的不提醒', async () => {
    const user = await openWeapons()
    await waitFor(() => expect(get).toHaveBeenCalledWith('/rulebooks/weapon-holders/coc'))
    await user.click(screen.getByText('弓箭'))
    await user.click(screen.getByRole('button', { name: /删除/ }))
    expect(warning).not.toHaveBeenCalled()
    await user.click(screen.getByText('斧头'))
    await user.click(screen.getByRole('button', { name: /删除/ }))
    expect(warning).toHaveBeenCalledWith(expect.stringContaining('有 3 个角色持有这把武器'))
  })

  it('新增一行没填主键时不提交，指出是哪一行', async () => {
    const user = await openWeapons()
    await user.click(screen.getByRole('button', { name: /新增/ }))
    await user.click(screen.getByRole('button', { name: /保存武器表/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent('第 3 行还没填主键')
    expect(put).not.toHaveBeenCalled()
  })

  it('服务端逐行报错时原样列出', async () => {
    put.mockRejectedValue(new Error('第 3 行（坏枪）伤害：必填\n第 4 行：x 重复了'))
    const user = await openWeapons()
    await user.click(screen.getByRole('button', { name: /新增/ }))
    await user.type(screen.getByLabelText('名称'), '坏枪')
    await user.click(screen.getByRole('button', { name: /保存武器表/ }))
    const alert = await screen.findByRole('alert')
    expect(within(alert).getAllByRole('listitem')).toHaveLength(2)
  })

  it('有未保存的改动时不能切到别的表', async () => {
    const user = await openWeapons()
    await user.click(screen.getByText('弓箭'))
    await user.type(screen.getByLabelText('伤害'), '+1')
    expect(screen.getByRole('tab', { name: '职业表' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: '放弃改动' }))
    expect(screen.getByRole('tab', { name: '职业表' })).toBeEnabled()
  })

  it('改过的行可以一键恢复官方原样', async () => {
    weaponsView = {
      ...weaponsView, rows: [{ ...BOW, dam: '9D9' }, AXE],
      status: { 弓箭: 'modified', 斧头: 'official' }, originals: { 弓箭: BOW },
    }
    const user = await openWeapons()
    await user.click(screen.getByText('弓箭'))
    await user.click(screen.getByRole('button', { name: /恢复官方/ }))
    expect(screen.getByLabelText('伤害')).toHaveValue('1D6+半DB')
  })

  it('模组层读写模组自己的接口', async () => {
    get.mockImplementation((path: string) =>
      Promise.resolve(path.endsWith('/manifest') ? MANIFEST : view()))
    render(<RuleDataEditor scope={{ ruleSystem: 'coc', moduleId: 'm1' }} />)
    await screen.findByText('会计')
    expect(get).toHaveBeenCalledWith('/modules/m1/rule-data/skills')
    expect(screen.getByText(/随模组走/)).toBeInTheDocument()
  })
})
