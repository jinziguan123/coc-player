import { render, screen, waitFor } from '@testing-library/react'
import { toast } from 'sonner'
import { describe, expect, it } from 'vitest'
import { Toaster } from './toaster'

describe('Toaster', () => {
  it('手机端顶部偏移带安全区，不往底部导航上压', async () => {
    render(<Toaster />)
    // sonner 的容器属性和偏移量都要等真的有 toast 时才写到 DOM 上
    toast('测试提示')
    expect(await screen.findByText('测试提示')).toBeInTheDocument()
    await waitFor(() =>
      expect(document.querySelector('[data-sonner-toaster]')).not.toBeNull(),
    )
    const toaster = document.querySelector('[data-sonner-toaster]') as HTMLElement

    // sonner 把 mobileOffset 写成 --mobile-offset-* 供窄屏媒体查询使用
    expect(toaster.style.getPropertyValue('--mobile-offset-top')).toBe(
      'calc(env(safe-area-inset-top, 0px) + 12px)',
    )
    expect(toaster.style.getPropertyValue('--mobile-offset-left')).toBe('12px')
    expect(toaster.style.getPropertyValue('--mobile-offset-right')).toBe('12px')
    // 位置仍在顶部——底部留给六项导航，toast 不去压它
    expect(toaster).toHaveAttribute('data-y-position', 'top')
    expect(toaster).toHaveAttribute('data-x-position', 'right')
  })
})
