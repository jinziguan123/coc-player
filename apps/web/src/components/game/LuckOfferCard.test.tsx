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
    expect(screen.getByRole('button', { name: '花 5 点补上' })).toBeInTheDocument()
    expect(screen.getByText(/当前幸运 45/)).toBeInTheDocument()
    expect(screen.getByText(/不计技能成长/)).toBeInTheDocument()
  })

  it('买或不买都要点一下——这张卡停住了整条结算链', async () => {
    const onDecide = vi.fn()
    render(
      <LuckOfferCard actor="陈守一" skill="侦查" cost={5} available={45} mine onDecide={onDecide} />,
    )

    await userEvent.click(screen.getByRole('button', { name: '花 5 点补上' }))
    expect(onDecide).toHaveBeenCalledWith('spend')

    await userEvent.click(screen.getByRole('button', { name: '放弃' }))
    expect(onDecide).toHaveBeenCalledWith('give_up')
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

describe('燃运重骰（村规）', () => {
  it('没开这条村规时不出现这个按钮', () => {
    render(
      <LuckOfferCard actor="陈守一" skill="侦查" cost={5} available={45} mine onDecide={() => {}} />,
    )
    expect(screen.queryByRole('button', { name: /重掷/ })).not.toBeInTheDocument()
  })

  it('补不起时只剩重掷这条路——那正是它存在的意义', async () => {
    // 差 40 点、只有 30 点幸运：补差额买不起（cost=0），烧 10 点还烧得起
    const onDecide = vi.fn()
    render(
      <LuckOfferCard
        actor="陈守一" skill="侦查" cost={0} rerollCost={10} available={30} mine onDecide={onDecide}
      />,
    )

    expect(screen.queryByRole('button', { name: /补上/ })).not.toBeInTheDocument()
    expect(screen.getByText('陈守一的「侦查」失败了')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: '烧 10 点重掷' }))
    expect(onDecide).toHaveBeenCalledWith('reroll')
  })

  it('两条路都通时并排给，代价各自写清楚', () => {
    render(
      <LuckOfferCard
        actor="陈守一" skill="侦查" cost={5} rerollCost={10} available={45} mine onDecide={() => {}}
      />,
    )

    expect(screen.getByRole('button', { name: '花 5 点补上' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '烧 10 点重掷' })).toBeInTheDocument()
    // 重掷是买机会不是买成功，得说清楚，别让人以为烧了就稳过
    expect(screen.getByText(/新骰点照单全收/)).toBeInTheDocument()
  })
})
