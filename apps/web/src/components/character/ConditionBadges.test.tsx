import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const get = vi.fn()
vi.mock('@/api/client', () => ({ api: { get: (...a: unknown[]) => get(...a) } }))

import { CharacterPanel } from './CharacterPanel'
import { resetConditionLabelCache } from './useConditionLabels'

const base = {
  id: 'c1', name: '山田健太', base_attributes: {}, skills: {}, backstory: '', status: 'active', rule_system: 'coc',
}

beforeEach(() => {
  get.mockReset()
  resetConditionLabelCache()
})

describe('角色卡持续状态徽标', () => {
  it('bleeding 显示「流血」徽标', async () => {
    get.mockResolvedValue({ conditions: [{ key: 'bleeding', label: '流血', component: 'coc7.bleeding' }] })
    render(<CharacterPanel character={{ ...base, system_data: { conditions: ['bleeding'] } }} />)
    expect(await screen.findByText('流血')).toBeInTheDocument()
    expect(get).toHaveBeenCalledWith('/rulebooks/village-rules/coc/manifest')
  })

  it('插件声明的状态显示其中文名与通用图标', async () => {
    get.mockResolvedValue({ conditions: [{ key: 'poisoned', label: '中毒', component: 'plugin.poison' }] })
    const { container } = render(
      <CharacterPanel character={{ ...base, system_data: { conditions: ['poisoned'] } }} />,
    )
    expect(await screen.findByText('中毒')).toBeInTheDocument()
    expect(screen.queryByText('poisoned')).not.toBeInTheDocument()
    expect(container.querySelector('[data-condition="poisoned"] svg')).not.toBeNull()
  })

  it('manifest 拿不到时回落状态键', async () => {
    get.mockRejectedValue(new Error('offline'))
    render(<CharacterPanel character={{ ...base, system_data: { conditions: ['poisoned'] } }} />)
    expect(screen.getByText('poisoned')).toBeInTheDocument()
    await waitFor(() => expect(get).toHaveBeenCalled())
  })

  it('没有状态时不渲染徽标、也不拉 manifest', () => {
    const { container } = render(<CharacterPanel character={{ ...base, system_data: {} }} />)
    expect(container.querySelector('[data-condition]')).toBeNull()
    expect(get).not.toHaveBeenCalled()
  })

  it('同一规则系统多个面板只请求一次', async () => {
    get.mockResolvedValue({ conditions: [{ key: 'bleeding', label: '流血', component: 'coc7.bleeding' }] })
    const ch = { ...base, system_data: { conditions: ['bleeding'] } }
    render(<><CharacterPanel character={ch} /><CharacterPanel character={{ ...ch, id: 'c2' }} /></>)
    expect(await screen.findAllByText('流血')).toHaveLength(2)
    expect(get).toHaveBeenCalledTimes(1)
  })
})
