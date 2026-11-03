import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { RulebookTome, type Rulebook } from './RulebookTome'

const base: Rulebook = {
  id: 'b1', title: '守秘人规则书', rule_system: 'coc', page_count: 400, chunk_count: 3273,
  status: 'ready', embed_model: 'm', error: '',
}

const renderTome = (b: Partial<Rulebook>) =>
  render(<RulebookTome book={{ ...base, ...b }} index={0} onDelete={() => {}} />)

describe('RulebookTome', () => {
  it('建索引中显示进度与进度条', () => {
    renderTome({ status: 'indexing', progress: { stage: 'embedding', done: 640, total: 3273, queue_position: 0 } })
    expect(screen.getByText('建索引 640/3273')).toBeInTheDocument()
    expect(screen.getByRole('progressbar', { name: /导入进度/ })).toHaveAttribute('aria-valuenow', '20')
    expect(screen.queryByText(/片段/)).not.toBeInTheDocument()
  })

  it('排队中显示名次，解析中显示阶段', () => {
    renderTome({ status: 'indexing', progress: { stage: 'queued', done: 0, total: 0, queue_position: 2 } })
    expect(screen.getByText('排队中（第 2 位）')).toBeInTheDocument()
  })

  it('旧版索引盖「旧版索引」章，删除按钮常在', () => {
    renderTome({ index_outdated: true, has_source: false })
    expect(screen.getByText('旧版索引')).toHaveAttribute('title', expect.stringContaining('重新上传'))
    expect(screen.getByRole('button', { name: '删除规则书「守秘人规则书」' })).toBeInTheDocument()
  })
})
