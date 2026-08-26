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
  get.mockResolvedValue({ options: {}, effective: RAW })
  put.mockResolvedValue({ rule_system: 'coc', options: {}, effective: RAW })
})

describe('村规面板', () => {
  it('按规则系统读取，回显后端算好的生效值', async () => {
    get.mockResolvedValue({ options: { critical_max: 5 }, effective: { ...RAW, critical_max: 5 } })
    render(<VillageRulesPanel ruleSystem="coc" />)

    await waitFor(() => expect(screen.getByDisplayValue('5')).toBeInTheDocument())
    expect(get).toHaveBeenCalledWith('/rulebooks/village-rules/coc')
  })

  it('幸运消费关着时不显示它的细则，开了才展开', async () => {
    render(<VillageRulesPanel ruleSystem="coc" />)
    await waitFor(() => expect(screen.getByText('幸运消费')).toBeInTheDocument())
    expect(screen.queryByText('单次上限')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('checkbox', { name: '幸运消费' }))
    await waitFor(() => expect(screen.getByText('单次上限')).toBeInTheDocument())
    expect(screen.getByText('战斗中可用')).toBeInTheDocument()
  })

  it('保存时整份提交，由后端负责钳区间与只存差异', async () => {
    render(<VillageRulesPanel ruleSystem="coc" />)
    await waitFor(() => expect(screen.getByText('大成功阈值')).toBeInTheDocument())

    await userEvent.click(screen.getByRole('button', { name: '保存村规' }))

    await waitFor(() => expect(put).toHaveBeenCalledWith(
      '/rulebooks/village-rules/coc',
      { options: RAW },
    ))
  })

  it('换一套规则系统会重新拉它自己的村规', async () => {
    const { rerender } = render(<VillageRulesPanel ruleSystem="coc" />)
    await waitFor(() => expect(get).toHaveBeenCalledWith('/rulebooks/village-rules/coc'))

    rerender(<VillageRulesPanel ruleSystem="dnd5e" />)
    await waitFor(() => expect(get).toHaveBeenCalledWith('/rulebooks/village-rules/dnd5e'))
  })
})
