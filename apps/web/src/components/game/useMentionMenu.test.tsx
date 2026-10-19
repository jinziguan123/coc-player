import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'

import { useMentionMenu } from './useMentionMenu'
import type { MentionCandidate } from './mentionSources'

const CANDIDATES: MentionCandidate[] = [
  { kind: 'character', label: '艾琳' },
  { kind: 'location', label: '地下室', hint: '当前' },
  { kind: 'skill', label: '侦查', hint: '70' },
]

/** 输入框的最小宿主：与 GameSessionPage 里那段接线同形（受控 + 光标回填 + 高度重算）。 */
function Harness(props: { candidates?: MentionCandidate[] }) {
  const [text, setText] = useState('')
  const menu = useMentionMenu({
    candidates: props.candidates ?? CANDIDATES,
    icons: { character: <i data-testid="ic-who" />, location: <i data-testid="ic-where" />, skill: <i data-testid="ic-skill" /> },
    onPick: ({ text: next, caret }) => {
      setText(next)
      // 同页面里的做法：受控赋值会把光标甩到串尾，选中后要把光标放回去。
      // 菜单状态用「我们想要的光标位」同步，而不是渲染后从 DOM 读——省掉一帧的错位。
      menu.sync({ value: next, selectionStart: caret })
      requestAnimationFrame(() => {
        const el = document.getElementById('ta') as HTMLTextAreaElement
        el.focus()
        el.setSelectionRange(caret, caret)
      })
    },
  })
  return (
    <div>
      {menu.palette}
      <textarea
        id="ta"
        aria-label="发言"
        value={text}
        onChange={(e) => { setText(e.target.value); menu.sync(e.target) }}
        onClick={(e) => menu.sync(e.currentTarget)}
        onBlur={() => menu.close()}
        onKeyDown={(e) => { menu.onKeyDown(e, text) }}
      />
    </div>
  )
}

const ta = () => document.getElementById('ta') as HTMLTextAreaElement

describe('@ 补全菜单', () => {
  it('打 @ 弹出候选，默认高亮第一条', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    await user.type(ta(), '@')
    const rows = screen.getAllByRole('option')
    expect(rows.length).toBeGreaterThan(1)
    expect(rows[0]).toHaveAttribute('aria-selected', 'true')
  })

  it('继续打字按名字过滤，图标按类型给', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    await user.type(ta(), '@地下')
    const rows = screen.getAllByRole('option')
    expect(rows).toHaveLength(1)
    expect(rows[0]).toHaveTextContent('地下室')
    expect(rows[0]).toHaveTextContent('当前')
    expect(rows[0].querySelector('[data-testid="ic-where"]')).toBeTruthy()
  })

  it('↑↓ 换高亮；点选一项就把触发段换成名字、菜单随即收起', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    await user.type(ta(), '@')
    expect(screen.getAllByRole('option')).toHaveLength(3)
    await user.keyboard('{ArrowDown}')
    expect(screen.getAllByRole('option')[1]).toHaveAttribute('aria-selected', 'true')
    await user.keyboard('{ArrowUp}')
    expect(screen.getAllByRole('option')[0]).toHaveAttribute('aria-selected', 'true')
    // 用点击而不是 Enter 确认：Enter 那条路已经由下一例覆盖（它验的是"不会顺手发出去"）。
    await user.click(screen.getAllByRole('option')[0])
    // 中文名后不补空格；打完这一项之后没有活跃的触发段，菜单自己收起。
    expect(ta().value).toBe('@艾琳')
    expect(screen.queryByRole('listbox')).toBeNull()
  })

  it('Enter 选中不会把这句话发出去——菜单先消费掉这一下', async () => {
    const user = userEvent.setup()
    const sent: string[] = []
    function Sender() {
      const [text, setText] = useState('')
      const menu = useMentionMenu({
        candidates: CANDIDATES,
        icons: { character: null, location: null, skill: null },
        onPick: ({ text: next }) => setText(next),
      })
      return (
        <div>
          {menu.palette}
          <textarea
            id="ta" aria-label="发言" value={text}
            onChange={(e) => { setText(e.target.value); menu.sync(e.target) }}
            onKeyDown={(e) => {
              if (menu.onKeyDown(e, text)) return
              if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sent.push(text) }
            }}
          />
        </div>
      )
    }
    render(<Sender />)
    await user.type(ta(), '@艾琳')
    await user.keyboard('{Enter}')
    expect(sent).toEqual([])
    expect(ta().value).toBe('@艾琳')
  })

  it('候选里没有的东西：不弹菜单，但 @ 照常是普通字符', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    await user.type(ta(), '@不存在的人')
    expect(screen.queryByRole('listbox')).toBeNull()
    expect(ta().value).toBe('@不存在的人')
  })

  it('@ 前面是词内字符（邮箱）时不弹', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    await user.type(ta(), 'lin@')
    expect(screen.queryByRole('listbox')).toBeNull()
  })

  it('打字过程中出现空格即收菜单', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    await user.type(ta(), '@艾琳 你好')
    expect(screen.queryByRole('listbox')).toBeNull()
  })

  it('失焦即收起，文本不受影响', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    await user.type(ta(), '@地下')
    expect(screen.getByRole('listbox')).toBeTruthy()
    // jsdom 的 el.blur() 不一定会派发 focusout（React 的 onBlur 听的是它），直接发事件。
    fireEvent.blur(ta())
    expect(screen.queryByRole('listbox')).toBeNull()
    expect(ta().value).toBe('@地下')
  })

  it('Esc 收起菜单但不改文本', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    await user.type(ta(), '@地下')
    expect(screen.getByRole('listbox')).toBeTruthy()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('listbox')).toBeNull()
    expect(ta().value).toBe('@地下')
  })
})