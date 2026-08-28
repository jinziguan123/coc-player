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
    guestLabel: '',
    setGuestLabel: vi.fn(),
    joinWaiting: false,
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
    render(<JoinRoomPanel setup={setupWith({ hostAddr: 'coc:xu4vabc:K7M9PQ2R' })} />)
    expect(screen.getByRole('button', { name: '加入' })).toBeEnabled()
  })

  it('识别邀请码后说明要走内置直连、且需对方同意', () => {
    render(<JoinRoomPanel setup={setupWith({ hostAddr: 'COC:xu4vabc' })} />)
    expect(screen.getByText(/内置直连/)).toBeInTheDocument()
    expect(screen.getByText(/同意/)).toBeInTheDocument()
  })

  it('改名前发出去的 trpg: 邀请码仍然按邀请码对待', () => {
    // 码是发给别人的字符串，项目改名不该让对方手里那张当场作废。
    render(<JoinRoomPanel setup={setupWith({ hostAddr: 'trpg:xu4vabc:K7M9PQ2R' })} />)
    expect(screen.getByText(/内置直连/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '加入' })).toBeEnabled()
  })

  it('普通主机地址不显示直连提示，也仍然要求房间码', () => {
    render(<JoinRoomPanel setup={setupWith({ hostAddr: '192.168.1.5' })} />)
    expect(screen.queryByText(/内置直连/)).toBeNull()
    expect(screen.getByRole('button', { name: '加入' })).toBeDisabled()
  })

  it('粘了邀请码才要求填自己的名字——房主那边只看得到一串公钥', () => {
    render(<JoinRoomPanel setup={setupWith({ hostAddr: 'coc:xu4vabc' })} />)
    expect(screen.getByLabelText('你的名字')).toBeInTheDocument()
  })

  it('普通主机地址不问名字（局域网加入不经过批准）', () => {
    render(<JoinRoomPanel setup={setupWith({ hostAddr: '192.168.1.5' })} />)
    expect(screen.queryByLabelText('你的名字')).toBeNull()
  })

  it('等待房主同意时说明在等什么，并禁掉重复点击', () => {
    // 首次加入可能卡一两分钟，不说清楚会被当成卡死。
    render(
      <JoinRoomPanel
        setup={setupWith({ hostAddr: 'coc:xu4vabc:K7M9PQ2R', joinWaiting: true })}
      />,
    )
    expect(screen.getByText(/正在等房主同意/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '等待中…' })).toBeDisabled()
  })

  it('粘贴带房间码的邀请码时立刻拆出房间码填进去', async () => {
    // 连上房主后也会从握手拿到房间码，但那要等对方点同意；粘贴当下就填好，
    // 用户才看得见「这码里已经带了房间号」。
    const setJoinCode = vi.fn()
    const user = userEvent.setup()
    render(<JoinRoomPanel setup={setupWith({ setJoinCode })} />)

    await user.click(screen.getByPlaceholderText(/邀请码/))
    await user.paste('coc:xu4vabcdefg:k7m9pq2r')
    expect(setJoinCode).toHaveBeenCalledWith('K7M9PQ2R')
  })

  it('邀请码没带房间码时不动房间码栏', async () => {
    const setJoinCode = vi.fn()
    const user = userEvent.setup()
    render(<JoinRoomPanel setup={setupWith({ setJoinCode })} />)

    await user.click(screen.getByPlaceholderText(/邀请码/))
    await user.paste('coc:xu4vabcdefg')
    expect(setJoinCode).not.toHaveBeenCalled()
  })

  it('容忍聊天软件带来的引号', async () => {
    const setJoinCode = vi.fn()
    const user = userEvent.setup()
    render(<JoinRoomPanel setup={setupWith({ setJoinCode })} />)

    await user.click(screen.getByPlaceholderText(/邀请码/))
    await user.paste('「coc:xu4vabcdefg:K7M9PQ2R」')
    expect(setJoinCode).toHaveBeenCalledWith('K7M9PQ2R')
  })

  it('普通主机地址不会被当成邀请码拆解', async () => {
    const setJoinCode = vi.fn()
    const user = userEvent.setup()
    render(<JoinRoomPanel setup={setupWith({ setJoinCode })} />)

    await user.click(screen.getByPlaceholderText(/邀请码/))
    await user.paste('192.168.1.5:8756')
    expect(setJoinCode).not.toHaveBeenCalled()
  })

  it('点加入会触发 joinRoom', async () => {
    const joinRoom = vi.fn()
    const user = userEvent.setup()
    render(<JoinRoomPanel setup={setupWith({ joinCode: 'K7M9PQ2R', joinRoom })} />)

    await user.click(screen.getByRole('button', { name: '加入' }))
    expect(joinRoom).toHaveBeenCalled()
  })
})
