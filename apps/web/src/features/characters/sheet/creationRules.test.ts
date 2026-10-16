import { describe, expect, it } from 'vitest'
import {
  ageBand, applyAge, canPick, checkBuild, DEFAULT_RULES, deductLeft, occupationPoints, pointUsage, requiredDeduction,
  skillRole, skillStart, type Attrs, type BuildState, type Occupation,
} from './creationRules'

// 与 server/tests/test_character_creation.py 用同一组数据与同样的断言
const BASE: Attrs = { STR: 50, CON: 60, SIZ: 60, DEX: 50, APP: 60, INT: 70, POW: 60, EDU: 70 }
const TABLE: Record<string, number> = {
  会计: 5, 聆听: 20, 心理学: 10, 侦查: 25, 取悦: 15, 话术: 5, 恐吓: 15, 说服: 10, 神秘学: 5,
  潜行: 20, 攀爬: 20, 游泳: 20, 跳跃: 20, '格斗(斗殴)': 25, 克苏鲁神话: 0, 母语: 0, 闪避: 0, 信用评级: 0,
}
const SPECS = { 母语: { base_init: 0, items: [] }, 外语: { base_init: 1, items: [{ name: '拉丁文', init: 1 }] } }
const BARTENDER: Occupation = {
  name: '酒保', category: '服务业', credit_min: 8, credit_max: 25, skill_formula: 'EDU*2+APP*2',
  skills: ['会计', '格斗(斗殴)', '聆听', '心理学', '侦查'],
  groups: [{ label: '两项社交技能', pick: 2, options: ['取悦', '话术', '恐吓', '说服'] }], choices: 1,
}

function state(raise: Record<string, number> = {}, over: Partial<BuildState> = {}): BuildState {
  const attrs = (over.baseAttrs as Attrs) ?? BASE
  const skills: Record<string, number> = {}
  for (const k of Object.keys(TABLE)) skills[k] = skillStart(k, attrs, TABLE, SPECS)
  skills['信用评级'] = 10
  for (const [k, v] of Object.entries(raise)) skills[k] = k === '信用评级' ? v : skills[k] + v
  return {
    method: 'roll', baseAttrs: BASE, age: 25, deductions: {}, eduRolls: [], occ: BARTENDER,
    picks: ['取悦', '说服', '神秘学'], skills, quickBoosts: [], ...over,
  }
}
const problems = (s: BuildState, rules = DEFAULT_RULES) => checkBuild(s, rules, TABLE, SPECS).problems.map((p) => p.text)

describe('建卡规则', () => {
  it('合规的卡没有问题', () => {
    expect(problems(state({ 取悦: 40, 说服: 40, 神秘学: 30, 会计: 50, 聆听: 40, 潜行: 40, 信用评级: 20 }))).toEqual([])
  })

  it('兴趣点只够非本职；职业点不能补到非本职上', () => {
    expect(problems(state({ 潜行: 70, 攀爬: 71 })).join()).toContain('兴趣点超出')
  })

  it('本职技能可由剩余兴趣点补足', () => {
    expect(problems(state({ 取悦: 60, 说服: 60, 会计: 60, 聆听: 60, 神秘学: 20, 信用评级: 20 }))).toEqual([])
  })

  it('信用评级要在职业区间内', () => {
    expect(problems(state({ 信用评级: 30 })).join()).toContain('信用评级须在 8–25')
  })

  it('选择组数量不对要报', () => {
    expect(problems(state({}, { picks: ['取悦', '神秘学'] })).join()).toContain('两项社交技能：还需选 1 项')
  })

  it('建卡上限来自本桌规则；母语起始值高于上限不算违规', () => {
    const house = { ...DEFAULT_RULES, skill_cap_other: 50, skill_cap_occupation: 50 }
    expect(problems(state({ 潜行: 40 }), house).join()).toContain('潜行 超过建卡上限 50')
    expect(problems(state(), house)).toEqual([])
    expect(problems(state({ 母语: 5 }), house).join()).toContain('母语 超过建卡上限 50')
  })

  it('母语专精起始值 = 教育，不花点', () => {
    expect(skillStart('母语(中文)', BASE, TABLE, SPECS)).toBe(70)
    const usage = pointUsage(state(), TABLE, SPECS)!
    expect(usage.intUsed).toBe(0)
  })

  it('克苏鲁神话不能加点', () => {
    expect(problems(state({ 克苏鲁神话: 5 })).join()).toContain('克苏鲁神话 建卡时不能加点')
  })
})

describe('年龄修正', () => {
  it('40 岁起：教育增强 2 次、体能扣 5 自己分配、外貌 −5、移动力 −1', () => {
    const band = ageBand(45)
    expect([band.eduChecks, band.deduct, band.app, band.mov]).toEqual([2, 5, 5, 1])
    expect(deductLeft(45, { STR: 5 }, BASE)).toBe(0)
    const a = applyAge(BASE, 45, { STR: 5 }, [])
    expect([a.STR, a.APP]).toEqual([45, 55])
  })

  it('少年：力量/体型合计扣 5、教育 −5', () => {
    const a = applyAge(BASE, 17, { SIZ: 5 }, [])
    expect([a.SIZ, a.EDU]).toEqual([55, 65])
  })

  it('属性扣不动时以能扣的为准（每项最多扣到 1）', () => {
    const weak = { ...BASE, STR: 20, CON: 25, DEX: 30 }   // 三项最多能扣 19+24+29 = 72 < 80
    expect(requiredDeduction(85, weak)).toBe(72)
    expect(deductLeft(85, { STR: 19, CON: 24, DEX: 29 }, weak)).toBe(0)
  })

  it('年龄扣点没分配完要报', () => {
    expect(problems(state({}, { age: 45 })).join()).toContain('年龄扣点还差 5 点')
  })
})

describe('职业', () => {
  it('固定 / 选择组 / 任意的身份，组满了不能再勾', () => {
    expect(skillRole(BARTENDER, '格斗(斗殴)')).toBe('fixed')
    expect(skillRole(BARTENDER, '取悦')).toBe('group:0')
    expect(skillRole(BARTENDER, '潜行')).toBe('free')
    expect(canPick(BARTENDER, ['取悦', '说服'], '话术')).toBe(false)
    expect(canPick(BARTENDER, ['取悦'], '话术')).toBe(true)
  })

  it('职业点公式：| 取较高者', () => {
    expect(occupationPoints('EDU*2+STR|DEX*2', { ...BASE, DEX: 80 })).toBe(300)
  })
})

describe('快速车卡', () => {
  it('本职定值各用一次、四项兴趣 +20', () => {
    const base = { STR: 40, CON: 50, SIZ: 50, DEX: 50, APP: 60, INT: 60, POW: 70, EDU: 80 }
    const s = state({}, { method: 'quick', baseAttrs: base, quickBoosts: ['潜行', '攀爬', '游泳', '跳跃'] })
    Object.assign(s.skills, { 会计: 70, 聆听: 60, 心理学: 60, 侦查: 50, 取悦: 50, 说服: 50, '格斗(斗殴)': 40 })
    for (const k of ['潜行', '攀爬', '游泳', '跳跃']) s.skills[k] += 20
    s.skills['信用评级'] = 15
    expect(problems(s)).toEqual([])
    s.skills['聆听'] = 70
    expect(problems(s).join()).toContain('只能从')
  })
})
