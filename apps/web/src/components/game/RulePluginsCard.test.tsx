import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { RulePluginsCard } from './RulePluginsCard'

const get = vi.fn()
vi.mock('../../api/client', () => ({ api: { get: (...a: unknown[]) => get(...a) } }))

beforeEach(() => get.mockReset())

describe('规则插件卡片', () => {
  it('列出已加载与未加载的插件，未加载的写明原因', async () => {
    get.mockResolvedValue({
      directory: '/data/plugins', api_version: 1,
      plugins: [
        { file: 'wound_penalty.py', name: '受伤减值', version: '1.0', description: 'HP 低时多惩罚骰', status: 'loaded', error: '', components: ['example.wound-penalty'] },
        { file: 'broken.py', name: 'broken.py', version: '', description: '', status: 'failed', error: '导入失败：SyntaxError', components: [] },
      ],
    })
    render(<RulePluginsCard ruleSystem="coc" />)
    expect(await screen.findByText('受伤减值')).toBeInTheDocument()
    expect(screen.getByText('已加载')).toBeInTheDocument()
    expect(screen.getByText('导入失败：SyntaxError')).toBeInTheDocument()
    expect(get).toHaveBeenCalledWith('/rulebooks/plugins/coc')
  })

  it('把安全边界写在界面上', async () => {
    get.mockResolvedValue({ directory: '/data/plugins', api_version: 1, plugins: [] })
    render(<RulePluginsCard ruleSystem="coc" />)
    expect(await screen.findByText(/没有安装插件/)).toBeInTheDocument()
    expect(screen.getByText(/没有沙箱/)).toBeInTheDocument()
  })
})
