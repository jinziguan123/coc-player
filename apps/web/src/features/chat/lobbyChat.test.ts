import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const get = vi.fn()
vi.mock('@/api/client', () => ({ api: { get: (url: string) => get(url) } }))

import { mergeChat, useLobbyChat, type ChatLine } from './lobbyChat'

const L = (seq: number): ChatLine => ({ id: `e${seq}`, seq, name: '甲', content: `第${seq}句` })
const row = (seq: number) => ({ id: `e${seq}`, sequence_num: seq, actor_name: '甲', content: `第${seq}句` })

describe('mergeChat', () => {
  it('按 id 去重、按序号排、只留最后 max 条', () => {
    const prev = [L(1), L(3)]
    expect(mergeChat(prev, [L(3)])).toBe(prev)
    expect(mergeChat(prev, [L(2), L(4)]).map((x) => x.seq)).toEqual([1, 2, 3, 4])
    expect(mergeChat(prev, [L(2), L(4)], 2).map((x) => x.seq)).toEqual([3, 4])
  })
})

describe('useLobbyChat', () => {
  beforeEach(() => get.mockReset())

  it('首次拉最新一页，之后用 after_seq 补漏并与实时消息去重', async () => {
    get.mockResolvedValueOnce({ events: [row(1), row(2)] })
    const { result } = renderHook(() => useLobbyChat('s1'))
    await act(() => result.current.catchUp())
    expect(get.mock.calls[0][0]).toBe('/sessions/s1/events?types=ooc&limit=200')
    expect(result.current.loaded).toBe(true)

    // 断线前实时收到 3；断线期间又有 4、5
    act(() => result.current.onOoc({ id: 'e3', seq: 3, actor_name: '甲', content: '第3句' }))
    get.mockResolvedValueOnce({ events: [row(3), row(4)], has_more: true })
    get.mockResolvedValueOnce({ events: [row(5)], has_more: false })
    await act(() => result.current.catchUp())
    expect(get.mock.calls[1][0]).toContain('after_seq=2')
    expect(get.mock.calls[2][0]).toContain('after_seq=4')
    expect(result.current.lines.map((x) => x.seq)).toEqual([1, 2, 3, 4, 5])
  })

  it('拉取失败不抛、不标记已加载', async () => {
    get.mockRejectedValueOnce(new Error('断网'))
    const { result } = renderHook(() => useLobbyChat('s1'))
    await act(() => result.current.catchUp())
    expect(result.current.loaded).toBe(false)
  })
})
