import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { VillageRulesPanel } from './VillageRulesPanel'

const get = vi.fn()
const put = vi.fn()
vi.mock('../../api/client', () => ({
  api: {
    get: (...a: unknown[]) => get(...a),
    put: (...a: unknown[]) => put(...a),
  },
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const RAW = {
  critical_max: 1,
  fumble_rule: 'raw',
  dice_pool_cap: 2,
  luck_spend: false,
  luck_spend_max: 0,
  luck_spend_in_combat: true,
  luck_spend_blocks_improvement: true,
  major_wound_divisor: 2,
  insanity_rule: 'fifth_of_san',
  insanity_flat_threshold: 5,
  improvement: true,
}

beforeEach(() => {
  get.mockReset()
  put.mockReset()
  get.mockResolvedValue({ options: {}, effective: RAW, table_notes: '' })
  put.mockResolvedValue({ rule_system: 'coc', options: {}, effective: RAW, table_notes: '' })
})

describe('村规面板', () => {
  it('按规则系统读取，回显后端算好的生效值', async () => {
    get.mockResolvedValue({
      options: { critical_max: 5 }, effective: { ...RAW, critical_max: 5 }, table_notes: '',
    })
    render(<VillageRulesPanel ruleSystem="coc" />)

    await waitFor(() => expect(screen.getByDisplayValue('5')).toBeInTheDocument())
    expect(get).toHaveBeenCalledWith('/rulebooks/village-rules/coc')
  })

  it('幸运消费关着时不显示它的细则，开了才展开', async () => {
    render(<VillageRulesPanel ruleSystem="coc" />)
    await waitFor(() => expect(screen.getByText('幸运消费')).toBeInTheDocument())
    expect(screen.queryByText('单次上限')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('switch', { name: '幸运消费' }))
    await waitFor(() => expect(screen.getByText('单次上限')).toBeInTheDocument())
    expect(screen.getByText('战斗中可用')).toBeInTheDocument()
  })

  it('保存时整份提交，由后端负责钳区间与只存差异', async () => {
    render(<VillageRulesPanel ruleSystem="coc" />)
    await waitFor(() => expect(screen.getByText('大成功阈值')).toBeInTheDocument())

    await userEvent.click(screen.getByRole('button', { name: '保存村规' }))

    await waitFor(() => expect(put).toHaveBeenCalledWith(
      '/rulebooks/village-rules/coc',
      { options: RAW, table_notes: '', enabled: true },
    ))
  })

  it('桌面约定回显、可编辑，并把界限写在界面上', async () => {
    get.mockResolvedValue({ options: {}, effective: RAW, table_notes: '本局重调查轻战斗。' })
    render(<VillageRulesPanel ruleSystem="coc" />)

    const box = await screen.findByLabelText('桌面约定')
    expect(box).toHaveValue('本局重调查轻战斗。')
    // 界限必须写在界面上：不说清楚，玩家会在这里写「大失败只认 100」然后以为生效了
    expect(screen.getByText(/只影响怎么演，不改骰子结算/)).toBeInTheDocument()

    await userEvent.clear(box)
    await userEvent.type(box, 'NPC 死亡不可逆')
    await userEvent.click(screen.getByRole('button', { name: '保存村规' }))
    await waitFor(() => expect(put).toHaveBeenCalledWith(
      '/rulebooks/village-rules/coc',
      { options: RAW, table_notes: 'NPC 死亡不可逆', enabled: true },
    ))
  })

  it('换一套规则系统会重新拉它自己的村规', async () => {
    const { rerender } = render(<VillageRulesPanel ruleSystem="coc" />)
    await waitFor(() => expect(get).toHaveBeenCalledWith('/rulebooks/village-rules/coc'))

    rerender(<VillageRulesPanel ruleSystem="dnd5e" />)
    await waitFor(() => expect(get).toHaveBeenCalledWith('/rulebooks/village-rules/dnd5e'))
  })
})

describe('村规总开关', () => {
  it('关掉后保存：配置照旧整份提交，只是 enabled 变 false', async () => {
    // 关键在于**不清空**——玩家想先照规则原文跑一局试试，回头一开就全回来了。
    const user = userEvent.setup()
    get.mockResolvedValue({ options: {}, effective: RAW, table_notes: '', enabled: true })
    render(<VillageRulesPanel ruleSystem="coc" />)
    await screen.findByText('启用村规')

    await user.click(screen.getByRole('switch', { name: '启用村规' }))
    await user.click(screen.getByRole('button', { name: '保存村规' }))

    await waitFor(() => expect(put).toHaveBeenCalled())
    const [, body] = put.mock.calls[0] as [string, { enabled: boolean; options: unknown }]
    expect(body.enabled).toBe(false)
    expect(body.options).toEqual(RAW)      // 配置原样留着，没被清掉
  })

  it('后端说停用就照实回显，别让人以为还开着', async () => {
    get.mockResolvedValue({ options: {}, effective: RAW, table_notes: '', enabled: false })
    render(<VillageRulesPanel ruleSystem="coc" />)
    expect(await screen.findByRole('switch', { name: '启用村规' })).not.toBeChecked()
    expect(screen.getByText(/完全照规则书原文跑/)).toBeInTheDocument()
  })

  it('旧后端没有这个字段时按开着算——升级不该让人的村规突然失效', async () => {
    get.mockResolvedValue({ options: {}, effective: RAW, table_notes: '' })
    render(<VillageRulesPanel ruleSystem="coc" />)
    expect(await screen.findByRole('switch', { name: '启用村规' })).toBeChecked()
  })
})
