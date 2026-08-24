import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { CheckRequestCard } from './CheckRequestCard'

describe('投骰请求卡', () => {
  it('把 KP 给的检定缘由摆在投骰要求旁边', () => {
    render(
      <CheckRequestCard
        content="请 陈守一 进行一次「理智」检定"
        reason="病床下伸出的那截青灰色手指"
        actionable
        pending
        onRoll={() => {}}
      />,
    )

    expect(screen.getByText('请 陈守一 进行一次「理智」检定')).toBeInTheDocument()
    expect(screen.getByText('因：病床下伸出的那截青灰色手指')).toBeInTheDocument()
  })

  it('KP 没给缘由时不留空行', () => {
    render(
      <CheckRequestCard content="请 陈守一 进行一次「侦查」检定" actionable pending onRoll={() => {}} />,
    )

    expect(screen.queryByText(/^因：/)).not.toBeInTheDocument()
  })

  it('轮到我投时按钮可点，投完改显示已投骰', async () => {
    const onRoll = vi.fn()
    const { rerender } = render(
      <CheckRequestCard content="请 陈守一 进行一次「侦查」检定" actionable pending onRoll={onRoll} />,
    )
    await userEvent.click(screen.getByRole('button', { name: /投骰/ }))
    expect(onRoll).toHaveBeenCalledTimes(1)

    rerender(
      <CheckRequestCard content="请 陈守一 进行一次「侦查」检定" actionable pending={false} onRoll={onRoll} />,
    )
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(screen.getByText('已投骰')).toBeInTheDocument()
  })

  it('别人的检定不给我按钮', () => {
    render(
      <CheckRequestCard
        content="请 坂田桐时 进行一次「聆听」检定"
        actionable={false}
        pending
        onRoll={() => {}}
      />,
    )

    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })
})
