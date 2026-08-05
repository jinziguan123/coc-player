import { beforeEach, describe, expect, it } from 'vitest'
import { loadHandledSuggestions, markSuggestionHandled } from './travelSuggest'

describe('「要不要去」建议卡的已处理记录', () => {
  beforeEach(() => localStorage.clear())

  it('按会话隔离：另一局的记录不该让这一局的卡片消失', () => {
    markSuggestionHandled('s1', 'ev1')
    expect(loadHandledSuggestions('s1').has('ev1')).toBe(true)
    expect(loadHandledSuggestions('s2').has('ev1')).toBe(false)
  })

  it('累加而不是覆盖', () => {
    markSuggestionHandled('s1', 'ev1')
    const after = markSuggestionHandled('s1', 'ev2')
    expect([...after].sort()).toEqual(['ev1', 'ev2'])
    expect([...loadHandledSuggestions('s1')].sort()).toEqual(['ev1', 'ev2'])
  })

  it('脏数据不炸：读不出来就当没记过，卡片照常显示', () => {
    localStorage.setItem('trpg_travel_suggest_done:s1', '{不是 JSON')
    expect(loadHandledSuggestions('s1').size).toBe(0)
  })
})
