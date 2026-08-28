import { beforeEach, describe, expect, it, vi } from 'vitest'

const drive = vi.fn()
// 显式声明入参：vi.fn() 无参会被推断成零元组，之后读 mock.calls[0][0] 过不了 tsc
const driver = vi.fn((_config: unknown) => ({ drive }))
vi.mock('driver.js', () => ({ driver: (config: unknown) => driver(config) }))
vi.mock('driver.js/dist/driver.css', () => ({}))

const { startGameTour, hasSeenGameTour, markGameTourSeen } = await import('./gameTour')

type TourConfig = {
  steps: { element?: string; popover: { title: string; description: string } }[]
  skipMissingElement?: boolean
  nextBtnText?: string
  onDestroyed?: () => void
}

beforeEach(() => {
  localStorage.clear()
  driver.mockClear()
  drive.mockClear()
})

/** startGameTour 里为等 React 渲染出面板用了 rAF，测试里推一帧。 */
async function tick() {
  await new Promise((r) => requestAnimationFrame(() => r(null)))
}

describe('对局导览', () => {
  it('开跑前先把角色卡面板打开——否则那一步没东西可高亮', async () => {
    const onNeedSheet = vi.fn()
    startGameTour({ onNeedSheet })

    expect(onNeedSheet).toHaveBeenCalledTimes(1)
    await tick()
    expect(drive).toHaveBeenCalledTimes(1)
  })

  it('缺席的元素跳过而不是卡住：不同席位看到的按钮本就不一样', async () => {
    startGameTour()
    await tick()

    const config = driver.mock.calls[0][0] as unknown as TourConfig
    expect(config.skipMissingElement).toBe(true)
  })

  it('按钮是中文的', async () => {
    startGameTour()
    await tick()

    expect((driver.mock.calls[0][0] as unknown as TourConfig).nextBtnText).toBe('下一步')
  })

  it('每一步都得有话说；带 element 的步骤必须指向 data-tour 锚点', async () => {
    startGameTour()
    await tick()

    const { steps } = driver.mock.calls[0][0] as unknown as TourConfig
    expect(steps.length).toBeGreaterThan(5)
    for (const step of steps) {
      expect(step.popover.title).toBeTruthy()
      expect(step.popover.description).toBeTruthy()
      // 锚点一律走 data-tour：复用 class 的话，样式一重构导览就悄悄指错地方
      if (step.element) expect(step.element).toMatch(/^\[data-tour="[a-z-]+"\]$/)
    }
  })

  it('只教此刻真在页面上的东西——投骰、战斗那些留给一次性提示', async () => {
    startGameTour()
    await tick()

    const { steps } = driver.mock.calls[0][0] as unknown as TourConfig
    const anchors = steps.map((s) => s.element).filter(Boolean)
    for (const dynamic of ['check-request', 'dice-result', 'luck-offer', 'combat']) {
      expect(anchors).not.toContain(`[data-tour="${dynamic}"]`)
    }
  })

  it('走完就记住，下次进对局不再自动弹', async () => {
    expect(hasSeenGameTour()).toBe(false)

    startGameTour()
    await tick()
    const config = driver.mock.calls[0][0] as unknown as TourConfig
    config.onDestroyed?.()

    expect(hasSeenGameTour()).toBe(true)
  })

  it('读不到 localStorage 时当作已看过——宁可不弹，也别每次进来都弹', () => {
    const getItem = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('隐私模式')
    })
    expect(hasSeenGameTour()).toBe(true)
    getItem.mockRestore()
  })

  it('写不进 localStorage 也不该抛——引导失败不能拖垮对局', () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('配额满')
    })
    expect(() => markGameTourSeen()).not.toThrow()
    setItem.mockRestore()
  })
})
