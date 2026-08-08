import { useState } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi } from 'vitest'
import { NumberField } from './number-field'

/** 受控包装：还原真实用法（父组件持有 number 状态）。 */
function Harness({
  initial = 25,
  min,
  max,
  fallback,
  onValue,
}: {
  initial?: number
  min?: number
  max?: number
  fallback?: number
  onValue?: (v: number | undefined) => void
}) {
  const [value, setValue] = useState<number | undefined>(initial)
  return (
    <NumberField
      aria-label="年龄"
      value={value}
      min={min}
      max={max}
      fallback={fallback}
      onChange={(v) => {
        setValue(v)
        onValue?.(v)
      }}
    />
  )
}

describe('NumberField', () => {
  it('输入过程中不改写用户打的字', async () => {
    // 这是这个组件存在的理由：年龄框限定 15..90，旧实现在 onChange 里 clamp，
    // 想输 30 时第一个字符 3 就被钳成 15，用户根本打不出目标值。
    const user = userEvent.setup()
    render(<Harness initial={25} min={15} max={90} fallback={25} />)
    const input = screen.getByLabelText('年龄') as HTMLInputElement

    await user.clear(input)
    expect(input.value).toBe('')          // 清空后就该是空的，不是跳回 25

    await user.type(input, '3')
    expect(input.value).toBe('3')         // 3 < 15，但那是中间态，不能拦

    await user.type(input, '0')
    expect(input.value).toBe('30')
  })

  it('失焦时才夹取到范围内', async () => {
    const user = userEvent.setup()
    render(<Harness initial={25} min={15} max={90} fallback={25} />)
    const input = screen.getByLabelText('年龄') as HTMLInputElement

    await user.clear(input)
    await user.type(input, '5')
    expect(input.value).toBe('5')         // 输入中不动
    await user.tab()
    expect(input.value).toBe('15')        // 失焦才夹到下限
  })

  it('失焦时夹取上限', async () => {
    const user = userEvent.setup()
    render(<Harness initial={25} min={15} max={90} fallback={25} />)
    const input = screen.getByLabelText('年龄') as HTMLInputElement

    await user.clear(input)
    await user.type(input, '200')
    await user.tab()
    expect(input.value).toBe('90')
  })

  it('清空后失焦回落到 fallback', async () => {
    const user = userEvent.setup()
    const onValue = vi.fn()
    render(<Harness initial={25} min={15} max={90} fallback={25} onValue={onValue} />)
    const input = screen.getByLabelText('年龄') as HTMLInputElement

    await user.clear(input)
    expect(onValue).toHaveBeenLastCalledWith(undefined)   // 输入中如实上报「空」
    await user.tab()
    expect(input.value).toBe('25')
    expect(onValue).toHaveBeenLastCalledWith(25)
  })

  it('没给 fallback 时允许留空', async () => {
    const user = userEvent.setup()
    render(<Harness initial={25} />)
    const input = screen.getByLabelText('年龄') as HTMLInputElement

    await user.clear(input)
    await user.tab()
    expect(input.value).toBe('')
  })

  it('范围内的值原样保留', async () => {
    const user = userEvent.setup()
    render(<Harness initial={25} min={15} max={90} fallback={25} />)
    const input = screen.getByLabelText('年龄') as HTMLInputElement

    await user.clear(input)
    await user.type(input, '30')
    await user.tab()
    expect(input.value).toBe('30')
  })

  it('不下发 HTML min/max，避免浏览器对中间态弹校验气泡', () => {
    render(<Harness initial={25} min={15} max={90} />)
    const input = screen.getByLabelText('年龄')
    expect(input).not.toHaveAttribute('min')
    expect(input).not.toHaveAttribute('max')
  })

  it('外部值变化会同步进来（如重掷属性）', async () => {
    function Outer() {
      const [v, setV] = useState<number | undefined>(50)
      return (
        <>
          <NumberField aria-label="力量" value={v} onChange={setV} />
          <button onClick={() => setV(80)}>重掷</button>
        </>
      )
    }
    const user = userEvent.setup()
    render(<Outer />)
    const input = screen.getByLabelText('力量') as HTMLInputElement
    expect(input.value).toBe('50')
    await user.click(screen.getByRole('button', { name: '重掷' }))
    expect(input.value).toBe('80')
  })
})
