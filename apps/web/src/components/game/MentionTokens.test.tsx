import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { MentionStrip, MentionText } from './MentionTokens'
import type { MentionCandidate } from './mentionSources'

const CANDIDATES: MentionCandidate[] = [
  { kind: 'character', label: '纳迪娅·哈桑' },
  { kind: 'location', label: '甬道' },
  { kind: 'skill', label: '侦查' },
]

describe('MentionText', () => {
  it('认识的提到渲染成小签，正文原样留着', () => {
    render(<MentionText text="我对@纳迪娅·哈桑说去@甬道" candidates={CANDIDATES} />)
    expect(screen.getByText('@纳迪娅·哈桑')).toHaveClass('mention-chip', 'mention-chip--character')
    expect(screen.getByText('@甬道')).toHaveClass('mention-chip--location')
    expect(screen.getByText('我对')).toBeTruthy()
    expect(screen.getByText('说去')).toBeTruthy()
  })

  it('认不出来的 @ 不当 token，原样留在正文里', () => {
    render(<MentionText text="钱花在@什么地方" candidates={CANDIDATES} />)
    expect(screen.queryByText('@什么地方')).toBeNull()
    expect(screen.getByText('钱花在@什么地方')).toBeTruthy()
  })

  it('没有提到的普通句子照原样渲染', () => {
    render(<MentionText text="我推开门" candidates={CANDIDATES} />)
    expect(screen.getByText('我推开门')).toBeTruthy()
  })
})

describe('MentionStrip', () => {
  it('草稿里提到过就列出来（去重）', () => {
    render(<MentionStrip text="先去@甬道，再叫上@纳迪娅·哈桑，@甬道见" candidates={CANDIDATES} />)
    expect(screen.getByText('已提到')).toBeTruthy()
    expect(screen.getByText('@甬道')).toBeTruthy()
    expect(screen.getByText('@纳迪娅·哈桑')).toBeTruthy()
  })

  it('没有提到时整条不渲染（不占高度）', () => {
    const { container } = render(<MentionStrip text="我推开门" candidates={CANDIDATES} />)
    expect(container.querySelector('.mention-strip')).toBeNull()
  })
})
