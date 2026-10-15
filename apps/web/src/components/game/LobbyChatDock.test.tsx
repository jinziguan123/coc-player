import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { LobbyChatDock, type ChatLine } from './LobbyChatDock'

const line = (i: number): ChatLine => ({ id: String(i), name: '甲', content: `第 ${i} 句` })
const base = { typingName: '', canSpeak: true, onSend: vi.fn(), onTyping: vi.fn() }

describe('LobbyChatDock', () => {
  beforeEach(() => localStorage.clear())

  it('选人阶段（compact）点浮标能展开成浮层，再收起回浮标', () => {
    const { container } = render(<LobbyChatDock {...base} lines={[line(1)]} compact />)
    fireEvent.click(screen.getByLabelText('展开大厅聊天'))
    expect(screen.getByText('第 1 句')).toBeInTheDocument()
    expect(container.querySelector('.chat-dock--overlay')).not.toBeNull()
    fireEvent.click(screen.getByLabelText('收起大厅聊天'))
    expect(screen.getByLabelText('展开大厅聊天')).toBeInTheDocument()
  })

  it('选人阶段的临时展开不改写平时的开合偏好，退出选人即收起', () => {
    localStorage.setItem('trpg.lobbyChat.open', '0')
    const { rerender, container } = render(<LobbyChatDock {...base} lines={[]} compact />)
    fireEvent.click(screen.getByLabelText('展开大厅聊天'))
    expect(localStorage.getItem('trpg.lobbyChat.open')).toBe('0')
    rerender(<LobbyChatDock {...base} lines={[]} compact={false} />)
    expect(container.querySelector('.chat-dock')).toBeNull()
  })

  it('选人阶段浮标收着时，新消息计入未读', () => {
    const { rerender } = render(<LobbyChatDock {...base} lines={[line(1)]} compact />)
    rerender(<LobbyChatDock {...base} lines={[line(1), line(2), line(3)]} compact />)
    expect(screen.getByLabelText('展开大厅聊天')).toHaveTextContent('2')
  })

  it('平时展开着，非专注模式渲染为普通聊天卡', () => {
    const { container } = render(<LobbyChatDock {...base} lines={[line(1)]} />)
    expect(container.querySelector('.chat-dock')).not.toBeNull()
    expect(container.querySelector('.chat-dock--overlay')).toBeNull()
  })
})
