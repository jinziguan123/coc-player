import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { GameEntryChooser } from './GameEntryChooser'

function setup(onClose = vi.fn()) {
  const onCreate = vi.fn()
  const onJoin = vi.fn()
  render(<GameEntryChooser open onCreate={onCreate} onJoin={onJoin} onClose={onClose} />)
  return { onClose, onCreate, onJoin }
}

describe('GameEntryChooser', () => {
  it('closes when mousedown starts on the backdrop', () => {
    const { onClose } = setup()
    const backdrop = document.querySelector('.entry-backdrop') as HTMLElement

    fireEvent.mouseDown(backdrop)

    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('ignores a click that is only released on the backdrop', () => {
    const { onClose } = setup()
    const backdrop = document.querySelector('.entry-backdrop') as HTMLElement
    const card = screen.getAllByRole('button')[0]

    fireEvent.mouseDown(card)
    fireEvent.click(backdrop)

    expect(onClose).not.toHaveBeenCalled()
  })
})
