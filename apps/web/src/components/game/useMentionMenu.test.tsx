import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'

import { useMentionMenu } from './useMentionMenu'
import type { MentionCandidate } from './mentionSources'

const CANDIDATES: MentionCandidate[] = [
  { kind: 'character', label: '艾琳' },
  { kind: 'location', label: '地下室', hint: '当前' },
  { kind: 'location', label: '旧宅门厅', hint: '已到过' },
  { kind: 'skill', label: '侦查', hint: '70' },
]

/** 输入框的最小宿主：与 GameSessionPage 里那段接线同形（受控 + 光标回填）。 */
function Harness(props: { candidates?: MentionCandidate[] }) {
  const [text, setText] = useState('')
  const menu = useMentionMenu({
    candidates: props.candidates ?? CANDIDATES,
    icons: {
      character: <i data-testid="ic-who" />,
      location: <i data-testid="ic-where" />,
      skill: <i data-testid="ic-skill" />,
    },
    onPick: ({ text: next, caret }) => {
      setText(next)
      // 同页面里的做法：受控赋值会把光标甩到串尾，选中后要把光标放回去。
      // 菜单状态用「我们想要的光标位」同步，而不是渲染后从 DOM 读——省掉一帧的错位。
      menu.sync({ value: next, selectionStart: caret })
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
        onKeyDown={(e) => {
          if (menu.onKeyDown(e, text)) return
          // 没被菜单消费掉的 Enter＝真的按了发送：往这里记一笔，用来断言「选中不会顺手发出」。
          if (e.key === 'Enter' && !e.shiftKey) sent.push(text)
        }}
      />
    </div>
  )
}

/** 记录「真的发出去了」的那些 Enter；每个用例开头清一次。 */
let sent: string[] = []
const ta = () => document.getElementById('ta') as HTMLTextAreaElement
const rows = () => screen.getAllByRole('option').map((r) => r.textContent || '')

describe('@ 补全：一级（分组入口）', () => {
  it('打 @ 只给三个入口，不直接铺开名字', async () => {
    sent = []
    const user = userEvent.setup()
    render(<Harness />)
    await user.type(ta(), '@')
    expect(rows()).toHaveLength(3)
    expect(rows()[0]).toContain('队友')
    expect(rows()[1]).toContain('已知地点')
    expect(rows()[2]).toContain('技能')
    // 每行右边给出这一组有几项，省得点进去才发现是空的
    expect(rows()[1]).toContain('2 项')
    expect(screen.getAllByRole('option')[0]).toHaveAttribute('aria-selected', 'true')
  })

  it('没有内容的组不列入口（这里只有队友，就不该出现「技能」）', async () => {
    const user = userEvent.setup()
    render(<Harness candidates={[{ kind: 'character', label: '艾琳' }]} />)
    await user.type(ta(), '@')
    expect(rows()).toHaveLength(1)
    expect(rows()[0]).toContain('队友')
  })
})

describe('@ 补全：二级（具体值）', () => {
  it('↑↓ 选组、Enter 进组：列出该组的值，头部写明是哪一组', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    await user.type(ta(), '@')
    await user.keyboard('{ArrowDown}')
    expect(screen.getAllByRole('option')[1]).toHaveAttribute('aria-selected', 'true')
    await user.keyboard('{Enter}')
    // 顺序与 buildCandidates 一致：当前所在的地点排最前（这里给的就是「地下室」在前）
    expect(rows()).toEqual(['地下室当前', '旧宅门厅已到过'])
    expect(screen.getByText('已知地点')).toBeTruthy()
    expect(screen.getByText('Esc 返回')).toBeTruthy()
  })

  it('二级里 Esc 退回一级，高亮停回原来那一组', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    await user.type(ta(), '@')
    await user.keyboard('{ArrowDown}{Enter}')      // 进「已知地点」
    expect(rows()).toHaveLength(2)
    await user.keyboard('{Escape}')
    expect(rows()).toHaveLength(3)                 // 回到三个入口
    expect(screen.getAllByRole('option')[1]).toHaveAttribute('aria-selected', 'true')
    expect(ta().value).toBe('@')                   // 文本没被动过
  })

  it('← 与 Esc 同义：都是退回一级', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    await user.type(ta(), '@')
    await user.keyboard('{Enter}{ArrowLeft}')
    expect(rows()).toHaveLength(3)
  })

  it('进组后点具体项就把触发段换成名字', async () => {
    sent = []
    const user = userEvent.setup()
    render(<Harness />)
    await user.type(ta(), '@')
    await user.keyboard('{Enter}')                // 进「队友」
    await user.click(screen.getAllByRole('option')[0])
    expect(ta().value).toBe('@艾琳')
    expect(screen.queryByRole('listbox')).toBeNull()
    expect(sent).toEqual([])
  })

  it('组内搜不到就退回全局，并说明这是「全部」', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    await user.type(ta(), '@')
    await user.keyboard('{Enter}')                // 先在「队友」里
    await user.type(ta(), '地')                    // 队友里没有「地」
    expect(rows()).toEqual(['地下室当前'])
    expect(screen.getByText('全部')).toBeTruthy()
  })
})

describe('@ 补全：直接打字跳过一级', () => {
  it('打 @地下 直接给地点项，不必先点分组', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    await user.type(ta(), '@地下')
    expect(rows()).toEqual(['地下室当前'])
    expect(screen.queryByText('已知地点')).toBeNull()   // 没进二级头部：这是一次直接过滤
  })

  it('按名字过滤时图标按类型给', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    await user.type(ta(), '@地下')
    const row = screen.getAllByRole('option')[0]
    expect(row.querySelector('[data-testid="ic-where"]')).toBeTruthy()
  })

  it('Enter 选中不会把这句话发出去——菜单先消费掉这一下', async () => {
    sent = []
    const user = userEvent.setup()
    render(<Harness />)
    await user.type(ta(), '@艾琳')
    await user.keyboard('{Enter}')
    expect(sent).toEqual([])
    expect(ta().value).toBe('@艾琳')
  })
})

describe('@ 补全：收起与失效', () => {
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
