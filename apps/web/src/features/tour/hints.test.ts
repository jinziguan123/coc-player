import { beforeEach, describe, expect, it, vi } from 'vitest'

// 只测「什么时候该弹、什么时候该记」这套判断——driver 自己的渲染不是这里的职责。
const drive = vi.fn()
// 显式声明入参：vi.fn() 无参会被推断成零元组，之后读 mock.calls[0][0] 过不了 tsc
const driver = vi.fn((_config: unknown) => ({ drive }))
vi.mock('driver.js', () => ({ driver: (config: unknown) => driver(config) }))
vi.mock('driver.js/dist/driver.css', () => ({}))

const { showHintOnce, hasSeenHint, markHintSeen, resetHints } = await import('./hints')

beforeEach(() => {
  localStorage.clear()
  document.body.innerHTML = ''
  document.body.className = ''
  driver.mockClear()
  drive.mockClear()
})

function mount(attr: string) {
  document.body.innerHTML = `<div data-tour="${attr}">卡片</div>`
}

describe('一次性就地提示', () => {
  it('目标出现且没看过 → 弹一次并记住', () => {
    mount('check-request')
    showHintOnce('check-request', '[data-tour="check-request"]')

    expect(drive).toHaveBeenCalledTimes(1)
    expect(hasSeenHint('check-request')).toBe(true)
  })

  it('看过就不再弹——这是「一次性」的全部意义', () => {
    mount('check-request')
    markHintSeen('check-request')

    showHintOnce('check-request', '[data-tour="check-request"]')
    expect(drive).not.toHaveBeenCalled()
  })

  it('目标还没出现 → 不弹也不记，下次它真出现时还能弹', () => {
    showHintOnce('luck-offer', '[data-tour="luck-offer"]')

    expect(drive).not.toHaveBeenCalled()
    expect(hasSeenHint('luck-offer')).toBe(false)
  })

  it('已有导览在跑 → 让位，且**不能**记成已看过', () => {
    mount('dice-result')
    document.body.classList.add('driver-active')   // 开场导览正开着

    showHintOnce('dice-result', '[data-tour="dice-result"]')

    expect(drive).not.toHaveBeenCalled()
    // 关键：记了就永远丢了这次教学
    expect(hasSeenHint('dice-result')).toBe(false)

    document.body.classList.remove('driver-active')
    showHintOnce('dice-result', '[data-tour="dice-result"]')
    expect(drive).toHaveBeenCalledTimes(1)
  })

  it('每条提示各记各的，互不影响', () => {
    mount('combat')
    showHintOnce('combat', '[data-tour="combat"]')

    expect(hasSeenHint('combat')).toBe(true)
    expect(hasSeenHint('split-party')).toBe(false)
  })

  it('弹出的内容取自该 key 的文案，且高亮的就是给定的目标', () => {
    mount('luck-offer')
    showHintOnce('luck-offer', '[data-tour="luck-offer"]')

    const config = driver.mock.calls[0][0] as unknown as {
      steps: { element: string; popover: { title: string; description: string } }[]
    }
    expect(config.steps[0].element).toBe('[data-tour="luck-offer"]')
    expect(config.steps[0].popover.title).toContain('幸运')
    // 代价要写在提示里：花掉不回来、买来的成功不算成长
    expect(config.steps[0].popover.description).toContain('不算技能成长')
  })

  it('resetHints 清空全部记录（给测试与「重看引导」用）', () => {
    mount('combat')
    showHintOnce('combat', '[data-tour="combat"]')
    expect(hasSeenHint('combat')).toBe(true)

    resetHints()
    expect(hasSeenHint('combat')).toBe(false)
  })
})
