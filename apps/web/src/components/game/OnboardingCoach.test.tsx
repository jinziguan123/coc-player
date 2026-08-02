import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { OnboardingCoach } from './OnboardingCoach'

describe('新手引导 / 操作速查', () => {
  it('首次进对局从第一页走，末页是速查', async () => {
    render(<OnboardingCoach onClose={vi.fn()} />)
    expect(screen.getByText('直接用自己的话写行动')).toBeInTheDocument()

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: '下一步' }))
    await user.click(screen.getByRole('button', { name: '下一步' }))
    await user.click(screen.getByRole('button', { name: '下一步' }))

    expect(screen.getByText('操作速查')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '开始游戏' })).toBeInTheDocument()
  })

  it('从顶栏问号打开时直接落在速查页，不必先点三次「下一步」', () => {
    render(<OnboardingCoach onClose={vi.fn()} startAtReference />)

    expect(screen.getByText('操作速查')).toBeInTheDocument()
    // 玩到一半最想查的几件事都在这一页
    expect(screen.getByText('怎么读骰子')).toBeInTheDocument()
    expect(screen.getByText('暗投是什么')).toBeInTheDocument()
    expect(screen.getByText('主动申请检定')).toBeInTheDocument()
    expect(screen.getByText('身上有什么')).toBeInTheDocument()
    // 已经在玩的人不该看到「跳过」，收尾按钮也不是「开始游戏」
    expect(screen.queryByRole('button', { name: '跳过' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '知道了' })).toBeInTheDocument()
  })

  it('速查页写明「不在道具清单上的东西就是没有」', () => {
    render(<OnboardingCoach onClose={vi.fn()} startAtReference />)
    expect(screen.getByText(/不在清单上的东西就是没有/)).toBeInTheDocument()
  })
})
