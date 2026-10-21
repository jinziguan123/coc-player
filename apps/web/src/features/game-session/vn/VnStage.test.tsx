import { act, fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { ChatMessage } from '@/stores/sessionStore'
import { VnStage } from './VnStage'

const narr = (id: string, content: string): ChatMessage => ({ id, type: 'narration', content })
const row = (msg: ChatMessage) => <div key={msg.id} data-testid={`row-${msg.id}`}>{msg.content}</div>

function stage(messages: ChatMessage[], extra: Partial<Parameters<typeof VnStage>[0]> = {}) {
  return render(
    <VnStage messages={messages} renderMessage={row} pendingCheckIds={new Set()} streaming={false} {...extra} />,
  )
}

/** 点一下对话框：打字中则补全本页，已补全则翻到下一页 / 下一段 */
const clickBox = () => fireEvent.click(screen.getByRole('region', { name: '对话框' }))

describe('对话框模式舞台', () => {
  it('从最新一段开始读；点一下补全打字，历史里点「从这里读」回到更早的一段', () => {
    stage([narr('a', '雾从港口漫上来。'), narr('b', '钟楼敲了十三下。')])
    clickBox()
    expect(screen.getByRole('region', { name: '对话框' })).toHaveTextContent('钟楼敲了十三下。')

    fireEvent.click(screen.getByTitle('历史记录（Esc 关闭）'))
    expect(screen.getByTestId('row-a')).toBeInTheDocument()
    fireEvent.click(screen.getAllByText('从这里读')[0])
    clickBox()
    expect(screen.getByRole('region', { name: '对话框' })).toHaveTextContent('雾从港口漫上来。')
    expect(screen.getByText('还有 1 段')).toBeInTheDocument()

    clickBox()
    clickBox()
    expect(screen.getByRole('region', { name: '对话框' })).toHaveTextContent('钟楼敲了十三下。')
  })

  it('读到最新时新到的一段直接接上；没读完时只记「还有 N 段」', () => {
    const { rerender } = stage([narr('a', '第一段。')])
    clickBox()
    const next = [narr('a', '第一段。'), narr('b', '第二段。')]
    rerender(<VnStage messages={next} renderMessage={row} pendingCheckIds={new Set()} streaming={false} />)
    clickBox()
    expect(screen.getByRole('region', { name: '对话框' })).toHaveTextContent('第二段。')

    // 回到第一段再来新消息：不把人拽走
    fireEvent.keyDown(window, { key: 'ArrowLeft' })
    act(() => {
      rerender(<VnStage messages={[...next, narr('c', '第三段。')]} renderMessage={row} pendingCheckIds={new Set()} streaming={false} />)
    })
    expect(screen.getByText('还有 2 段')).toBeInTheDocument()
  })

  it('检定请求是浮在对话框上方的卡片；读过但没掷完的一直挂着', () => {
    const check: ChatMessage = { id: 'c', type: 'system', content: '请投侦查', metadata: { check_request: true, id: 'k1' } }
    stage([narr('a', '你推开门。'), check, narr('b', '屋里很暗。')], { pendingCheckIds: new Set(['k1']) })
    expect(screen.getByTestId('row-c')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: '对话框' })).not.toHaveTextContent('请投侦查')
  })
})
