import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { LazyMotion, domAnimation } from 'motion/react'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import { Dialog, DialogContent, DialogTitle, DialogTrigger } from './dialog'

function Harness() {
  const [open, setOpen] = useState(false)
  return (
    <LazyMotion features={domAnimation} strict>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogTrigger>打开</DialogTrigger>
        <DialogContent aria-describedby={undefined}>
          <DialogTitle>标题</DialogTitle>
        </DialogContent>
      </Dialog>
    </LazyMotion>
  )
}

describe('Dialog 退场', () => {
  it('Esc 关掉：先播退场再卸载，焦点回到触发器', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    await user.click(screen.getByRole('button', { name: '打开' }))
    expect(screen.getByRole('dialog')).toBeInTheDocument()

    await user.keyboard('{Escape}')
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(screen.getByRole('button', { name: '打开' })).toHaveFocus()
  })

  it('非受控用法照常开合', async () => {
    const user = userEvent.setup()
    render(
      <Dialog>
        <DialogTrigger>开</DialogTrigger>
        <DialogContent aria-describedby={undefined}><DialogTitle>标题</DialogTitle></DialogContent>
      </Dialog>,
    )
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '开' }))
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })
})
