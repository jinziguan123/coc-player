import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { CheckResultCard } from './CheckResultCard'

describe('检定结果卡', () => {
  it('在公屏展示 SAN 减值和结算前后数值', () => {
    render(
      <CheckResultCard
        meta={{
          actor: '调查员',
          skill: 'SAN',
          skill_value: 50,
          old_san: 50,
          roll: 78,
          target: 50,
          outcome: '失败',
          san_loss: 4,
          new_san: 46,
          went_insane: false,
        }}
        blind={false}
        animClass=""
      />,
    )

    expect(screen.getByLabelText('SAN 减少 4，由 50 变为 46')).toBeInTheDocument()
    expect(screen.getByText('-4')).toBeInTheDocument()
    expect(screen.getByText('50 → 46')).toBeInTheDocument()
  })

  it('发生疯狂时展示公开状态', () => {
    render(
      <CheckResultCard
        meta={{
          actor: '调查员',
          skill: 'SAN',
          skill_value: 20,
          roll: 99,
          target: 20,
          outcome: '失败',
          san_loss: 5,
          new_san: 15,
          went_insane: true,
        }}
        blind={false}
        animClass=""
      />,
    )

    expect(screen.getByText('疯狂')).toBeInTheDocument()
  })
})

describe('检定结果卡：表盘与印章', () => {
  const base = { actor: '调查员', skill: '侦查', skill_value: 60, target: 60 }

  it('大成功盖一枚可读的印章，普通成功不盖——罕见才值得盖章', () => {
    const { rerender } = render(
      <CheckResultCard meta={{ ...base, roll: 1, outcome: 'critical' }} blind={false} animClass="" />,
    )
    expect(screen.getByRole('img', { name: '大成功' })).toBeInTheDocument()

    rerender(<CheckResultCard meta={{ ...base, roll: 30, outcome: 'success' }} blind={false} animClass="" />)
    expect(screen.queryByRole('img', { name: /大成功|大失败/ })).not.toBeInTheDocument()
  })

  it('暗投既不画表盘也不盖章：指针落点和印章都会泄露结果', () => {
    const { container } = render(
      <CheckResultCard meta={{ ...base, roll: 100, outcome: 'fumble' }} blind animClass="" />,
    )
    expect(container.querySelector('.brass-dial')).toBeNull()
    expect(screen.queryByRole('img', { name: '大失败' })).not.toBeInTheDocument()
  })
})
