import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { HistorySearchModal } from './HistorySearchModal'

const get = vi.fn()
vi.mock('../../api/client', () => ({ api: { get: (...a: unknown[]) => get(...a) } }))

function hit(seq: number, content: string, extra: Record<string, unknown> = {}) {
  return {
    id: `e${seq}`, sequence_num: seq, event_type: 'narration',
    actor_name: 'KP', content, created_at: '2026-08-09T12:34:00Z', ...extra,
  }
}

/** 后端返回 total 条，本页按 offset/limit 切。 */
function stubSearch(total: number, page: (offset: number) => unknown[]) {
  get.mockImplementation((url: string) => {
    const offset = Number(new URL(url, 'http://x').searchParams.get('offset') || 0)
    return Promise.resolve({ total, results: page(offset) })
  })
}

const props = { sessionId: 's1', onClose: vi.fn(), onJump: vi.fn() }

beforeEach(() => { get.mockReset(); props.onClose.mockReset(); props.onJump.mockReset() })

describe('HistorySearchModal', () => {
  it('关键词在结果里高亮出来', async () => {
    stubSearch(1, () => [hit(9, '一尊半埋在落叶里的地藏石像歪斜地立在那儿')])
    const user = userEvent.setup()
    render(<HistorySearchModal {...props} />)

    await user.type(screen.getByRole('textbox'), '地藏')
    await waitFor(() => expect(screen.getByText('地藏')).toBeInTheDocument())
    // 高亮用 <mark>，不是把整段文本重画一遍
    expect(screen.getByText('地藏').tagName).toBe('MARK')
  })

  it('一条里出现多次就高亮多次', async () => {
    stubSearch(1, () => [hit(9, '地藏在路旁。你走近那尊地藏。')])
    const user = userEvent.setup()
    render(<HistorySearchModal {...props} />)   // Modal 是 portal 到 body 的，从 body 上找

    await user.type(screen.getByRole('textbox'), '地藏')
    await waitFor(() => expect(document.body.querySelectorAll('mark')).toHaveLength(2))
  })

  it('标出事件类型与说话人', async () => {
    stubSearch(1, () => [hit(9, '这是地藏像', { event_type: 'dialogue', actor_name: '香澄澪' })])
    const user = userEvent.setup()
    render(<HistorySearchModal {...props} />)

    await user.type(screen.getByRole('textbox'), '地藏')
    await waitFor(() => expect(screen.getByText('台词')).toBeInTheDocument())
    expect(screen.getByText('香澄澪')).toBeInTheDocument()
  })

  it('默认由新到旧，可切成由旧到新', async () => {
    stubSearch(3, () => [hit(9, '地藏一')])
    const user = userEvent.setup()
    render(<HistorySearchModal {...props} />)

    await user.type(screen.getByRole('textbox'), '地藏')
    await waitFor(() => expect(screen.getByText('由新到旧')).toBeInTheDocument())
    expect(get.mock.calls.at(-1)![0]).toContain('order=desc')

    await user.click(screen.getByTitle('切换时间排序'))
    await waitFor(() => expect(get.mock.calls.at(-1)![0]).toContain('order=asc'))
    expect(screen.getByText('由旧到新')).toBeInTheDocument()
  })

  it('结果超过一页时给分页，翻页只取该页', async () => {
    stubSearch(20, (offset) => [hit(100 - offset, `第 ${offset} 页的地藏`)])
    const user = userEvent.setup()
    render(<HistorySearchModal {...props} />)

    await user.type(screen.getByRole('textbox'), '地藏')
    await waitFor(() => expect(screen.getByText('1 / 3')).toBeInTheDocument())
    expect(screen.getByRole('button', { name: /上一页/ })).toBeDisabled()

    await user.click(screen.getByRole('button', { name: /下一页/ }))
    await waitFor(() => expect(screen.getByText('2 / 3')).toBeInTheDocument())
    expect(get.mock.calls.at(-1)![0]).toContain('offset=8')
  })

  it('命中总数摆出来——不然不知道自己在多大的结果集里翻', async () => {
    stubSearch(42, () => [hit(9, '地藏')])
    const user = userEvent.setup()
    render(<HistorySearchModal {...props} />)

    await user.type(screen.getByRole('textbox'), '地藏')
    await waitFor(() => expect(screen.getByText('42')).toBeInTheDocument())
  })

  it('换关键词回到第一页', async () => {
    stubSearch(20, () => [hit(9, '地藏')])
    const user = userEvent.setup()
    render(<HistorySearchModal {...props} />)
    const box = screen.getByRole('textbox')

    await user.type(box, '地藏')
    await waitFor(() => expect(screen.getByText('1 / 3')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: /下一页/ }))
    await waitFor(() => expect(screen.getByText('2 / 3')).toBeInTheDocument())

    await user.clear(box)
    await user.type(box, '呼子')
    await waitFor(() => expect(get.mock.calls.at(-1)![0]).toContain('offset=0'))
  })

  it('点结果跳转并关闭', async () => {
    stubSearch(1, () => [hit(9, '地藏石像')])
    const user = userEvent.setup()
    render(<HistorySearchModal {...props} />)

    await user.type(screen.getByRole('textbox'), '地藏')
    await waitFor(() => expect(screen.getByTitle('跳转到该记录')).toBeInTheDocument())
    await user.click(screen.getByTitle('跳转到该记录'))
    expect(props.onJump).toHaveBeenCalledWith('e9')
    expect(props.onClose).toHaveBeenCalled()
  })

  it('无匹配时说清楚是哪个词没匹配上', async () => {
    stubSearch(0, () => [])
    const user = userEvent.setup()
    render(<HistorySearchModal {...props} />)

    await user.type(screen.getByRole('textbox'), '不存在的词')
    await waitFor(() => expect(screen.getByText(/没有匹配「不存在的词」/)).toBeInTheDocument())
  })

  it('没输入时不发请求', async () => {
    stubSearch(0, () => [])
    render(<HistorySearchModal {...props} />)
    await new Promise((r) => setTimeout(r, 350))
    expect(get).not.toHaveBeenCalled()
  })

  it('旧响应不覆盖新结果', async () => {
    // 翻到第二页那次故意慢，紧接着翻到第三页那次立刻回。不按请求序号丢弃的话，
    // 慢的那个后落地，会把用户已经翻到的第三页内容盖回第二页。
    let n = 0
    get.mockImplementation((url: string) => {
      const offset = Number(new URL(url, 'http://x').searchParams.get('offset') || 0)
      const body = { total: 20, results: [hit(offset, `第 ${offset} 页的地藏`)] }
      return ++n === 2
        ? new Promise((res) => setTimeout(() => res(body), 250))
        : Promise.resolve(body)
    })
    const user = userEvent.setup()
    render(<HistorySearchModal {...props} />)

    await user.type(screen.getByRole('textbox'), '地藏')
    await waitFor(() => expect(screen.getByText('1 / 3')).toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: /下一页/ }))   // 第 2 次：慢
    await user.click(screen.getByRole('button', { name: /下一页/ }))   // 第 3 次：快
    await new Promise((r) => setTimeout(r, 500))                       // 等慢的那次也回来

    expect(screen.getByText('3 / 3')).toBeInTheDocument()
    const card = screen.getByTitle('跳转到该记录')
    expect(within(card).getByText(/第 16 页的/)).toBeInTheDocument()   // 不是第 8 页
  })
})
