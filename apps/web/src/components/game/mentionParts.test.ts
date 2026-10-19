import { describe, expect, it } from 'vitest'

import { mentionsIn, splitMentions } from './mentionParts'
import type { MentionCandidate } from './mentionSources'

const CANDIDATES: MentionCandidate[] = [
  { kind: 'character', label: '艾琳' },
  { kind: 'character', label: '艾琳·卡特' },
  { kind: 'location', label: '地下室' },
  { kind: 'skill', label: '侦查' },
]

const kinds = (text: string) => splitMentions(text, CANDIDATES).map((p) => (p.type === 'mention' ? `@${p.name}` : p.text))

describe('splitMentions', () => {
  it('认得出的提到切成 token，夹在正文之间', () => {
    expect(kinds('我对@艾琳说：去看看@地下室')).toEqual([
      '我对', '@艾琳', '说：去看看', '@地下室',
    ])
  })

  it('给 token 带上类型，气泡据此上色', () => {
    const parts = splitMentions('用@侦查看看@地下室', CANDIDATES)
    expect(parts.filter((p) => p.type === 'mention')).toEqual([
      { type: 'mention', name: '侦查', kind: 'skill' },
      { type: 'mention', name: '地下室', kind: 'location' },
    ])
  })

  it('长名字优先：不会被同前缀的短名抢走', () => {
    expect(kinds('@艾琳·卡特来了')).toEqual(['@艾琳·卡特', '来了'])
  })

  it('认不出来的 @xxx 原样留着，不当 token', () => {
    expect(kinds('钱要花在@什么地方')).toEqual(['钱要花在@什么地方'])
    expect(kinds('发到 lin@example.com')).toEqual(['发到 lin@example.com'])
  })

  it('行首、标点后能认', () => {
    expect(kinds('@艾琳')).toEqual(['@艾琳'])
    expect(kinds('（@艾琳）')).toEqual(['（', '@艾琳', '）'])
  })

  it('没有 @ 或没有候选时，原样返回一段文本', () => {
    expect(kinds('我推开门')).toEqual(['我推开门'])
    expect(splitMentions('@艾琳', [])).toEqual([{ type: 'text', text: '@艾琳' }])
    expect(splitMentions('', CANDIDATES)).toEqual([])
  })
})

describe('mentionsIn', () => {
  it('去重且保持首次出现顺序', () => {
    const hits = mentionsIn('@地下室 之后回到@艾琳 再去@地下室', CANDIDATES)
    expect(hits.map((h) => (h.type === 'mention' ? h.name : ''))).toEqual(['地下室', '艾琳'])
  })

  it('一条都没有时给空表', () => {
    expect(mentionsIn('我推开门', CANDIDATES)).toEqual([])
  })
})
