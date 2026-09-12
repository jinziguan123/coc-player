import { fireEvent, render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Modal } from './modal'

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
