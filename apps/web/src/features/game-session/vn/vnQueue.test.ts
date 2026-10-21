import { describe, expect, it } from 'vitest'
import type { ChatMessage } from '@/stores/sessionStore'
import { buildStops, dwellMs, paginate, plainText, sceneImageOf, speakerOf } from './vnQueue'

const msg = (over: Partial<ChatMessage>): ChatMessage => ({ id: 'm', type: 'narration', content: '', ...over })

describe('对话框模式：一站一站', () => {
  it('场景配图只换背景不占一站；检定、掷骰、手书、去向建议是卡片；其余是对话框里的一段', () => {
    const stops = buildStops([
      msg({ id: '1', type: 'narration', content: '雨下得很大。' }),
      msg({ id: '2', type: 'system', content: '', metadata: { kind: 'illustration', icat: 'scene', image: '/api/images/a.jpg' } }),
      msg({ id: '3', type: 'system', content: '请投侦查', metadata: { check_request: true, id: 'c1' } }),
      msg({ id: '4', type: 'dice', content: '侦查 23/60 困难成功' }),
      msg({ id: '5', type: 'dialogue', content: '别碰那些字。', actor_name: '老哈米德' }),
      msg({ id: '6', type: 'system', content: '', metadata: { kind: 'handout', title: '日记残页' } }),
    ])
    expect(stops.map((s) => [s.msg.id, s.kind])).toEqual([
      ['1', 'line'], ['3', 'card'], ['4', 'card'], ['5', 'line'], ['6', 'card'],
    ])
    expect(stops[4].text).toContain('日记残页')
  })

  it('空正文跳过，但流式中的叙事（stream-*）即使还没字也要占一站', () => {
    const stops = buildStops([msg({ id: 'x', content: '  ' }), msg({ id: 'stream-1', content: '' })])
    expect(stops.map((s) => s.msg.id)).toEqual(['stream-1'])
  })

  it('叙事里的 Markdown 记号不进对话框', () => {
    expect(plainText('## 地窖\n他看见**一行**刻字，*很新*。\n> 引文')).toBe('地窖\n他看见一行刻字，很新。\n引文')
  })

  it('能认出场景配图', () => {
    expect(sceneImageOf(msg({ type: 'system', metadata: { kind: 'illustration', icat: 'scene', image: '/x.jpg' } }))).toBe('/x.jpg')
    expect(sceneImageOf(msg({ type: 'system', metadata: { kind: 'illustration', icat: 'clue', image: '/x.jpg' } }))).toBe('')
  })
})

describe('长文分页', () => {
  it('短文一页；长文尽量切在句末，每页不超过上限', () => {
    expect(paginate('短短一句。')).toEqual(['短短一句。'])
    const long = '甬道越往里越窄，火把的光被石壁吞去大半。'.repeat(8)
    const pages = paginate(long, 60)
    expect(pages.length).toBeGreaterThan(1)
    for (const p of pages) expect(p.length).toBeLessThanOrEqual(60)
    expect(pages.every((p) => p.endsWith('。'))).toBe(true)
    expect(pages.join('')).toBe(long)
  })

  it('一句本身超长时硬切', () => {
    const pages = paginate('啊'.repeat(250), 100)
    expect(pages.map((p) => p.length)).toEqual([100, 100, 50])
  })
})

describe('说话人与停留时间', () => {
  it('KP 旁白与系统不挂名牌；NPC 带立绘；玩家按 is_player', () => {
    expect(speakerOf(msg({ type: 'narration' })).kind).toBe('kp')
    expect(speakerOf(msg({ type: 'dialogue', actor_name: '诺特', metadata: { portrait: '/p.jpg' } }))).toEqual({ kind: 'npc', name: '诺特', portrait: '/p.jpg' })
    expect(speakerOf(msg({ type: 'action', actor_name: '李明', metadata: { is_player: true } })).kind).toBe('player')
  })

  it('自动播放的停留随字数增长，有上下限', () => {
    expect(dwellMs('')).toBe(1400)
    expect(dwellMs('字'.repeat(1000))).toBe(9000)
  })
})
