import { describe, expect, it } from 'vitest'

import {
  buildPartyByName,
  fmtTime,
  isSoloTable,
  sceneBackdropOf,
  npcHue,
  resolveActorKind,
  selectCombatLog,
  selectCombatResult,
  splitOOC,
  stripCommandTags,
} from './derive'

const msg = (over: Partial<Parameters<typeof selectCombatLog>[0][number]> = {}) => ({
  id: 'm1', type: 'dice', content: '内容', sequence_num: 10,
  metadata: { combat_log: true } as Record<string, unknown> | null,
  ...over,
})

describe('stripCommandTags', () => {
  it('去掉 KP 指令标签，正文保留', () => {
    expect(stripCommandTags('你推开门。[DICE_CHECK: skill=侦查]门后是走廊。'))
      .toBe('你推开门。门后是走廊。')
    expect(stripCommandTags('[SAY: who=管家]欢迎[/SAY]')).toBe('欢迎')
  })

  it('去掉裸 HTML 并压掉多余空行', () => {
    expect(stripCommandTags('<b>粗</b>体')).toBe('粗体')
    expect(stripCommandTags('一\n\n\n\n二')).toBe('一\n\n二')
  })
})

describe('splitOOC', () => {
  it('中英文小括号都算场外发言', () => {
    expect(splitOOC('我推门（等下，先看看）')).toEqual({ inChar: '我推门', ooc: '等下，先看看' })
    expect(splitOOC('I open it (wait)')).toEqual({ inChar: 'I open it', ooc: 'wait' })
  })

  it('多段 OOC 合并；无 OOC 时为空串', () => {
    expect(splitOOC('（一）走廊（二）').ooc).toBe('一 二')
    expect(splitOOC('只是普通行动')).toEqual({ inChar: '只是普通行动', ooc: '' })
  })
})

describe('npcHue', () => {
  it('同名恒定、异名多半不同、始终落在 0..359', () => {
    expect(npcHue('守墓人')).toBe(npcHue('守墓人'))
    expect(npcHue('守墓人')).not.toBe(npcHue('管家'))
    for (const n of ['', '甲', 'Alice', '很长很长的一个名字']) {
      expect(npcHue(n)).toBeGreaterThanOrEqual(0)
      expect(npcHue(n)).toBeLessThan(360)
    }
  })
})

describe('fmtTime', () => {
  it('无时间戳返回空串', () => {
    expect(fmtTime(undefined)).toBe('')
    expect(fmtTime(0)).toBe('')
  })

  it('补零到 HH:MM', () => {
    const d = new Date(2026, 6, 27, 9, 5)
    expect(fmtTime(d.getTime())).toBe('09:05')
  })
})

describe('队伍与身份', () => {
  const party = buildPartyByName([
    { character_name: '我的角色', is_mine: true, role: 'human' },
    { character_name: '队友甲', is_mine: false, role: 'ai' },
    { character_name: '别人', is_mine: false, role: 'human' },
    { character_name: null, is_mine: false, role: 'human' },  // 未选角色的空席
  ])

  it('只收录已选角色的席位', () => {
    expect(Object.keys(party).sort()).toEqual(['别人', '我的角色', '队友甲'])
  })

  it('按席位归属决定图标种类', () => {
    expect(resolveActorKind(party, '我的角色')).toBe('me')
    expect(resolveActorKind(party, '队友甲')).toBe('ai')
    expect(resolveActorKind(party, '别人')).toBe('human')
    expect(resolveActorKind(party, '守墓人')).toBe('npc')   // 不在队伍里 → NPC
    expect(resolveActorKind(party, undefined)).toBe('npc')
  })

  it('isPlayer 直接判为本人（历史消息带的标记）', () => {
    expect(resolveActorKind(party, '守墓人', true)).toBe('me')
  })
})

describe('selectCombatLog', () => {
  it('只收带 combat_log 标记的行', () => {
    const out = selectCombatLog(
      [msg({ id: 'a' }), msg({ id: 'b', metadata: {} }), msg({ id: 'c', metadata: null })],
      null,
    )
    expect(out.map((e) => e.id)).toEqual(['a'])
  })

  it('按本场起点挡掉上一场的结算行', () => {
    const out = selectCombatLog(
      [msg({ id: '旧', sequence_num: 5 }), msg({ id: '新', sequence_num: 15 })],
      10,
    )
    expect(out.map((e) => e.id)).toEqual(['新'])
  })

  it('起点为 null 时全收——重连时后端没给起点，与落库历史保持一致', () => {
    const out = selectCombatLog(
      [msg({ id: '旧', sequence_num: 5 }), msg({ id: '新', sequence_num: 15 })],
      null,
    )
    expect(out.map((e) => e.id)).toEqual(['旧', '新'])
  })

  it('区分 dice 与 system 两种行，且丢弃无 id 的', () => {
    const out = selectCombatLog(
      [msg({ id: 'd', type: 'dice' }), msg({ id: 's', type: 'system' }), msg({ id: undefined })],
      null,
    )
    expect(out).toEqual([
      { id: 'd', kind: 'dice', content: '内容' },
      { id: 's', kind: 'system', content: '内容' },
    ])
  })
})

describe('selectCombatResult', () => {
  const combat = {} as never
  const base = { combat, since: null, diceAnimating: new Set<string>(), revealedDice: new Set<string>() }

  it('不在战斗中时不显示', () => {
    expect(selectCombatResult({ ...base, combat: null, messages: [msg()] })).toBeNull()
  })

  it('取最后一条骰子事件', () => {
    const out = selectCombatResult({
      ...base,
      messages: [msg({ id: '早', content: '早' }), msg({ id: '晚', content: '晚' })],
    })
    expect(out?.content).toBe('晚')
  })

  it('3D 骰未落定的先跳过——否则结果会先于动画蹦出来剧透成败', () => {
    const out = selectCombatResult({
      ...base,
      messages: [msg({ id: '早', content: '早' }), msg({ id: '动画中', content: '晚' })],
      diceAnimating: new Set(['动画中']),
    })
    expect(out?.content).toBe('早')
  })

  it('动画已揭示则照常采用', () => {
    const out = selectCombatResult({
      ...base,
      messages: [msg({ id: '早', content: '早' }), msg({ id: '已揭示', content: '晚' })],
      diceAnimating: new Set(['已揭示']),
      revealedDice: new Set(['已揭示']),
    })
    expect(out?.content).toBe('晚')
  })

  it('越过本场起点即停，不显示开战前的旧结果', () => {
    const out = selectCombatResult({
      ...base,
      since: 10,
      messages: [msg({ id: '开战前', sequence_num: 5, content: '旧' })],
    })
    expect(out).toBeNull()
  })

  it('忽略非骰子事件与无 metadata 的骰子', () => {
    const out = selectCombatResult({
      ...base,
      messages: [
        msg({ id: 'd', content: '骰子' }),
        msg({ id: 'n', type: 'narration', content: '叙述' }),
        msg({ id: 'x', content: '没元数据', metadata: null }),
      ],
    })
    expect(out?.content).toBe('骰子')
  })
})

describe('isSoloTable', () => {
  const seat = (over: Partial<{ role: string; character_id: string | null; is_mine: boolean }> = {}) => ({
    is_mine: false, role: 'human', character_id: 'c1', ...over,
  })

  it('旧单人会话（无 participants）算独自开团', () => {
    expect(isSoloTable(undefined)).toBe(true)
    expect(isSoloTable([])).toBe(true)
  })

  it('只有自己一个真人 → 独自开团，AI 队友不算人头', () => {
    expect(isSoloTable([seat({ is_mine: true })])).toBe(true)
    expect(isSoloTable([
      seat({ is_mine: true }),
      seat({ role: 'ai', character_id: 'c2' }),
      seat({ role: 'ai', character_id: 'c3' }),
    ])).toBe(true)
  })

  it('两个真人 → 不是独自开团，措辞里要提到其他人', () => {
    expect(isSoloTable([seat({ is_mine: true }), seat({ character_id: 'c2' })])).toBe(false)
  })

  it('还没认领角色的空席位不算人头；真人 KP 席也不算', () => {
    expect(isSoloTable([seat({ is_mine: true }), seat({ character_id: null })])).toBe(true)
    expect(isSoloTable([seat({ is_mine: true }), seat({ role: 'kp', character_id: null })])).toBe(true)
  })
})

describe('sceneBackdropOf', () => {
  const 场景图 = (sceneId: string, image: string) =>
    ({ metadata: { kind: 'illustration', icat: 'scene', scene_id: sceneId, image } })
  const 别的消息 = { metadata: { icat: 'npc', portrait: '/api/images/p.jpg' } }
  /** 后端按「查看者自己的角色位置」算出的已知地点表 */
  const 在 = (id: string, image = '') => ({ id, current: true, image })
  const 别处 = (id: string, image = '') => ({ id, current: false, image })

  it('取自己所在场景那张图，不被别的场景或别类配图串台', () => {
    const msgs = [场景图('a', '/api/images/a.jpg'), 别的消息, 场景图('b', '/api/images/b.jpg')]
    expect(sceneBackdropOf(msgs, [别处('a'), 在('b')])).toBe('/api/images/b.jpg')
    // 回到走过的旧场景也能取回它原来的图
    expect(sceneBackdropOf(msgs, [在('a'), 别处('b')])).toBe('/api/images/a.jpg')
  })

  it('分头行动：各人按自己所在地渲染，不跟着房主锚点走', () => {
    const msgs = [场景图('地窖', '/api/images/cellar.jpg'), 场景图('阁楼', '/api/images/attic.jpg')]
    // 同一份消息流，两个查看者的 locations 不同 → 各看各的
    expect(sceneBackdropOf(msgs, [在('地窖'), 别处('阁楼')])).toBe('/api/images/cellar.jpg')
    expect(sceneBackdropOf(msgs, [别处('地窖'), 在('阁楼')])).toBe('/api/images/attic.jpg')
  })

  it('存量存档：那条「抵达」插图消息不在已加载分页里，回落 locations 上的 scene.image', () => {
    expect(sceneBackdropOf([], [在('a', '/api/images/from-module.jpg')]))
      .toBe('/api/images/from-module.jpg')
  })

  it('聊天流优先于 locations：刚重生成的图比后端那份新', () => {
    const msgs = [场景图('a', '/api/images/fresh.jpg')]
    expect(sceneBackdropOf(msgs, [在('a', '/api/images/stale.jpg')])).toBe('/api/images/fresh.jpg')
  })

  it('同场景重复到达取最后一张', () => {
    const msgs = [场景图('a', '/api/images/old.jpg'), 场景图('a', '/api/images/new.jpg')]
    expect(sceneBackdropOf(msgs, [在('a')])).toBe('/api/images/new.jpg')
  })

  it('没图就回落空串，界面自然退回主题底色', () => {
    expect(sceneBackdropOf([], [在('a')])).toBe('')
    expect(sceneBackdropOf([别的消息], [在('a')])).toBe('')
    expect(sceneBackdropOf([场景图('a', '/api/images/a.jpg')], undefined)).toBe('')
    // locations 还没拉回来 / 没有任何 current
    expect(sceneBackdropOf([场景图('a', '/api/images/a.jpg')], [别处('a')])).toBe('')
    // 图还在生成：只有占位没有 url
    expect(sceneBackdropOf([{ metadata: { icat: 'scene', scene_id: 'a' } }], [在('a')])).toBe('')
  })
})
