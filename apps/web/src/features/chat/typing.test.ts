import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const post = vi.fn(() => Promise.resolve({}))
vi.mock('@/api/client', () => ({ api: { post: (...a: unknown[]) => post(...(a as [])) } }))

import { TYPING_TTL_MS, typerKey, typingLabel, useTypers, useTypingNotifier } from './typing'

describe('typingLabel / typerKey', () => {
  it('按人数拼句', () => {
    expect(typingLabel([])).toBe('')
    expect(typingLabel(['甲'])).toBe('甲 正在输入…')
    expect(typingLabel(['甲', '乙'])).toBe('甲、乙 正在输入…')
    expect(typingLabel(['甲', '乙', '丙'])).toBe('甲、乙 等 3 人正在输入…')
  })
  it('有席位按席位认人，没有才看名字', () => {
    expect(typerKey({ actor_name: '玩家', metadata: { seat: 2 } })).toBe('seat:2')
    expect(typerKey({ actor_name: '玩家' })).toBe('name:玩家')
  })
})

describe('useTypers', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('同名「玩家」按席位区分：别人显示、自己不显示，多人并列', () => {
    const { result } = renderHook(() => useTypers({ seat: 1, name: '玩家' }))
    act(() => result.current.onTyping({ actor_name: '玩家', metadata: { seat: 1 } }))
    expect(result.current.label).toBe('')
    act(() => result.current.onTyping({ actor_name: '玩家', metadata: { seat: 2 } }))
    act(() => result.current.onTyping({ actor_name: '林医生', metadata: { seat: 3 } }))
    expect(result.current.label).toBe('玩家、林医生 正在输入…')
  })

  it('发言即清，超时自动清', () => {
    const { result } = renderHook(() => useTypers({ seat: 0 }))
    act(() => result.current.onTyping({ actor_name: '甲', metadata: { seat: 1 } }))
    act(() => result.current.onTyping({ actor_name: '乙', metadata: { seat: 2 } }))
    act(() => result.current.onSpoke('甲'))
    expect(result.current.label).toBe('乙 正在输入…')
    act(() => { vi.advanceTimersByTime(TYPING_TTL_MS + 1) })
    expect(result.current.label).toBe('')
  })
})

describe('useTypingNotifier', () => {
  beforeEach(() => { post.mockClear(); vi.useFakeTimers() })
  afterEach(() => vi.useRealTimers())

  it('只在变长时发、且节流；删字不发', () => {
    const { result } = renderHook(() => useTypingNotifier('s1', true))
    act(() => result.current('a'))
    act(() => result.current('ab'))
    expect(post).toHaveBeenCalledTimes(1)
    vi.advanceTimersByTime(2500)
    act(() => result.current('a'))
    expect(post).toHaveBeenCalledTimes(1)
    act(() => result.current('ab'))
    expect(post).toHaveBeenCalledTimes(2)
  })

  it('不能发言时不发', () => {
    const { result } = renderHook(() => useTypingNotifier('s1', false))
    act(() => result.current('a'))
    expect(post).not.toHaveBeenCalled()
  })
})
