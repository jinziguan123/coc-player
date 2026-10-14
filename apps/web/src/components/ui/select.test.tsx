import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { LazyMotion, domAnimation } from 'motion/react'
import { describe, expect, it } from 'vitest'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './select'

function Harness() {
  return (
    <LazyMotion features={domAnimation} strict>
      <Select defaultValue="a">
        <SelectTrigger aria-label="协议"><SelectValue /></SelectTrigger>
        <SelectContent>
          <SelectItem value="a">甲</SelectItem>
          <SelectItem value="b">乙</SelectItem>
        </SelectContent>
      </Select>
    </LazyMotion>
  )
}

describe('Select 退场', () => {
  it('关着时触发器照样显示当前值；关掉先播退场再卸载', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    const trigger = screen.getByRole('combobox', { name: '协议' })
    expect(trigger).toHaveTextContent('甲')

    await user.click(trigger)
    expect(screen.getByRole('listbox')).toBeInTheDocument()
    await user.keyboard('{Escape}')
    expect(screen.getByRole('listbox')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole('listbox')).not.toBeInTheDocument())
    expect(trigger).toHaveTextContent('甲')
  })
})
