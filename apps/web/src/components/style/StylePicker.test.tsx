import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import { StylePicker } from './StylePicker'

const OPTIONS = [
  { id: 'terse', label: '克制冷硬', hint: '短句、动词优先' },
  { id: 'dense', label: '古典绵密', hint: '长句铺陈' },
]

function Harness({ initial = '' }: { initial?: string }) {
  const [value, setValue] = useState(initial)
  return (
    <>
      <StylePicker
        kind="narrative" inheritLabel="跟随模组" options={OPTIONS}
        value={value} onChange={setValue}
      />
      <output data-testid="value">{value}</output>
    </>
  )
}

describe('StylePicker 取值约定', () => {
  it('选预设时写回预设 id，不是显示名', async () => {
    const user = userEvent.setup()
    render(<Harness />)

    await user.click(screen.getByRole('combobox'))
    await user.click(screen.getByRole('option', { name: /克制冷硬/ }))

    expect(screen.getByTestId('value')).toHaveTextContent('terse')
  })

  it('切到「自定义」后输入框留在页面上，输入的内容原样写回', async () => {
    const user = userEvent.setup()
    render(<Harness />)

    await user.click(screen.getByRole('combobox'))
    await user.click(screen.getByRole('option', { name: '自定义…' }))
    // 此时值还是空串。若靠 value 判断该不该显示输入框，这里就会立刻消失。
    const box = screen.getByRole('textbox')
    await user.type(box, '像武侠小说那样写')

    expect(screen.getByTestId('value')).toHaveTextContent('像武侠小说那样写')
  })

  it('切到「自定义」后光标直接落在输入框里', async () => {
    // 浏览器实测出来的坑：Radix 关下拉时把焦点还给触发器，正好盖掉 textarea 的 autoFocus，
    // 于是「选了自定义、直接敲字，什么都没进去」。上面那条用例显式点了输入框，测不出这个。
    const user = userEvent.setup()
    render(<Harness />)

    await user.click(screen.getByRole('combobox'))
    await user.click(screen.getByRole('option', { name: '自定义…' }))
    await waitFor(() => expect(screen.getByRole('textbox')).toHaveFocus())

    await user.keyboard('武侠味')
    expect(screen.getByTestId('value')).toHaveTextContent('武侠味')
  })

  it('非预设的既有值当自定义渲染：一进来就展开输入框并回填原文', () => {
    render(<Harness initial="像武侠小说那样写" />)
    expect(screen.getByRole('textbox')).toHaveValue('像武侠小说那样写')
  })

  it('「跟随模组」写回空串（Radix 不许空 value，故内部走哨兵，不能漏了翻译回来）', async () => {
    const user = userEvent.setup()
    render(<Harness initial="terse" />)

    await user.click(screen.getByRole('combobox'))
    await user.click(screen.getByRole('option', { name: '跟随模组' }))

    expect(screen.getByTestId('value')).toHaveTextContent('')
  })
})
