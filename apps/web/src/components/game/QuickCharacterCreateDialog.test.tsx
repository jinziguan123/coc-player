import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const post = vi.fn()
vi.mock('@/api/client', () => ({
  api: {
    get: vi.fn((url: string) => Promise.resolve(
      url.includes('creation-rules') ? {
        skill_cap_occupation: 90, skill_cap_other: 90, point_buy_total: 460,
        quick_attrs: [40, 50, 50, 50, 60, 60, 70, 80], quick_skills: [70, 60, 60, 50, 50, 50, 40, 40, 40],
        quick_boosts: 4, quick_boost: 20,
      } : url.includes('character-schema') ? { default_skills: { 侦查: 25 } } : [],
    )),
    post: (...a: unknown[]) => post(...a),
  },
  localApi: { post: (...a: unknown[]) => post(...a) },
  uploadFile: vi.fn(),
}))

import { QuickCharacterCreateDialog } from './QuickCharacterCreateDialog'

describe('大厅快速建卡', () => {
  beforeEach(() => { post.mockReset(); localStorage.clear() })

  it('默认快速车卡；关掉不建任何卡、不写角色页草稿', async () => {
    const onClose = vi.fn()
    render(<QuickCharacterCreateDialog open moduleId={null} onClose={onClose} onCreated={vi.fn()} />)
    await waitFor(() => expect(screen.getByRole('button', { name: '快速车卡' })).toHaveAttribute('aria-pressed', 'true'))
    expect(screen.getByText(/规则书「快速车卡」/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(onClose).toHaveBeenCalled()
    await new Promise((r) => setTimeout(r, 700))
    expect(post).not.toHaveBeenCalledWith('/characters', expect.anything())
    expect(localStorage.getItem('trpg_character_sheet_draft')).toBeNull()
  })
})
