import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { LuckOfferCard } from './LuckOfferCard'

describe('幸运消费询价卡', () => {
  it('明码标价：差几点、花掉后还剩多少、代价是什么', () => {
    render(
      <LuckOfferCard actor="陈守一" skill="侦查" cost={5} available={45} mine onDecide={() => {}} />,
    )

    expect(screen.getByText('陈守一的「侦查」差 5 点')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '花 5 点幸运' })).toBeInTheDocument()
    expect(screen.getByText(/当前幸运 45，花掉后剩 40/)).toBeInTheDocument()
    expect(screen.getByText(/不计技能成长/)).toBeInTheDocument()
  })

  it('买或不买都要点一下——这张卡停住了整条结算链', async () => {
    const onDecide = vi.fn()
    render(
      <LuckOfferCard actor="陈守一" skill="侦查" cost={5} available={45} mine onDecide={onDecide} />,
    )

    await userEvent.click(screen.getByRole('button', { name: '花 5 点幸运' }))
    expect(onDecide).toHaveBeenCalledWith(true)

    await userEvent.click(screen.getByRole('button', { name: '认了' }))
    expect(onDecide).toHaveBeenCalledWith(false)
  })

  it('别人的骰子只显示进度，不给按钮——花的是他自己的幸运', () => {
    render(
      <LuckOfferCard
        actor="坂田桐时" skill="聆听" cost={3} available={60} mine={false} onDecide={() => {}}
      />,
    )

    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(screen.getByText('等 坂田桐时 决定要不要动用幸运。')).toBeInTheDocument()
  })

  it('已提交时按钮禁用，防重复拍板', () => {
    render(
      <LuckOfferCard
        actor="陈守一" skill="侦查" cost={5} available={45} mine busy onDecide={() => {}}
      />,
    )

    for (const button of screen.getAllByRole('button')) {
      expect(button).toBeDisabled()
    }
  })
})
