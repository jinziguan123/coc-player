import { fireEvent, render, waitFor } from '@testing-library/react'
import { LazyMotion, domAnimation } from 'motion/react'
import { describe, expect, it, vi } from 'vitest'
import { Modal, ModalPresence } from './modal'

function parts() {
  const panel = document.querySelector('.modal-panel') as HTMLElement
  return { panel, mask: panel.parentElement as HTMLElement }
}

describe('Modal', () => {
  it('closes when mousedown starts on the backdrop itself', () => {
    const onClose = vi.fn()
    render(<Modal onClose={onClose}><span>content</span></Modal>)
    const { mask } = parts()

    fireEvent.mouseDown(mask)

    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('ignores a click that is only released on the backdrop', () => {
    const onClose = vi.fn()
    render(<Modal onClose={onClose}><span>content</span></Modal>)
    const { mask, panel } = parts()

    // Simulates mousedown inside a tab then layout shift, after which the
    // browser dispatches click on the common ancestor (the backdrop mask).
    fireEvent.mouseDown(panel)
    fireEvent.click(mask)

    expect(onClose).not.toHaveBeenCalled()
  })

  it('does not close when mousedown starts inside the panel', () => {
    const onClose = vi.fn()
    render(<Modal onClose={onClose}><span>content</span></Modal>)
    const { panel } = parts()

    fireEvent.mouseDown(panel)

    expect(onClose).not.toHaveBeenCalled()
  })
})

describe('ModalPresence 退场', () => {
  function Harness({ open, onClose }: { open: boolean; onClose: () => void }) {
    return (
      <LazyMotion features={domAnimation} strict>
        <ModalPresence>
          {open && <Modal onClose={onClose}><span>content</span></Modal>}
        </ModalPresence>
      </LazyMotion>
    )
  }

  it('关掉后先留着播完退场再卸载，退场途中不再响应 Esc / 遮罩', async () => {
    const onClose = vi.fn()
    const { rerender } = render(<Harness open onClose={onClose} />)
    expect(document.querySelector('.modal-panel')).not.toBeNull()

    rerender(<Harness open={false} onClose={onClose} />)
    // 退场中：还在 DOM 里，但遮罩不再拦点击
    const backdrop = document.querySelector('.modal-backdrop') as HTMLElement
    expect(backdrop).not.toBeNull()
    expect(backdrop.style.pointerEvents).toBe('none')
    fireEvent.keyDown(window, { key: 'Escape' })
    fireEvent.mouseDown(parts().mask)
    expect(onClose).not.toHaveBeenCalled()

    await waitFor(() => expect(document.querySelector('.modal-panel')).toBeNull())
  })
})
