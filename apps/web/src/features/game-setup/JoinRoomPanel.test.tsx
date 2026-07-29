import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { JoinRoomPanel } from './JoinRoomPanel'
import type { GameSetupState } from './useGameSetup'

/** 这个面板只读 setup 里的少数几项，其余按需补空。 */
function setupWith(overrides: Partial<GameSetupState> = {}): GameSetupState {
  return {
    connectedHost: '',
    disconnectHost: vi.fn(),
    hostAddr: '',
    setHostAddr: vi.fn(),
    joinCode: '',
    setJoinCode: vi.fn(),
    joinRoom: vi.fn(),
    ...overrides,
  } as unknown as GameSetupState
}

describe('加入房间面板', () => {
  it('地址栏空着时，没有房间码就不能加入', () => {
    render(<JoinRoomPanel setup={setupWith()} />)
    expect(screen.getByRole('button', { name: '加入' })).toBeDisabled()
  })

  it('填了房间码就能加入', () => {
    render(<JoinRoomPanel setup={setupWith({ joinCode: 'K7M9PQ2R' })} />)
    expect(screen.getByRole('button', { name: '加入' })).toBeEnabled()
  })

  it('粘了邀请码时房间码可以留空——邀请码本身就带着它', () => {
    // 曾经的 bug：按钮只看房间码栏，于是粘了完整邀请码反而点不动。
    render(<JoinRoomPanel setup={setupWith({ hostAddr: 'trpg:xu4vabc:K7M9PQ2R' })} />)
    expect(screen.getByRole('button', { name: '加入' })).toBeEnabled()
  })

  it('识别邀请码后说明要走内置直连、且需对方同意', () => {
    render(<JoinRoomPanel setup={setupWith({ hostAddr: 'TRPG:xu4vabc' })} />)
    expect(screen.getByText(/内置直连/)).toBeInTheDocument()
    expect(screen.getByText(/同意/)).toBeInTheDocument()
  })

  it('普通主机地址不显示直连提示，也仍然要求房间码', () => {
    render(<JoinRoomPanel setup={setupWith({ hostAddr: '192.168.1.5' })} />)
    expect(screen.queryByText(/内置直连/)).toBeNull()
    expect(screen.getByRole('button', { name: '加入' })).toBeDisabled()
  })

  it('点加入会触发 joinRoom', async () => {
    const joinRoom = vi.fn()
    const user = userEvent.setup()
    render(<JoinRoomPanel setup={setupWith({ joinCode: 'K7M9PQ2R', joinRoom })} />)

    await user.click(screen.getByRole('button', { name: '加入' }))
    expect(joinRoom).toHaveBeenCalled()
  })
})
