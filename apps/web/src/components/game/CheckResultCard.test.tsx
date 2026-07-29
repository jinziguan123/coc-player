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
