import { describe, expect, it } from 'vitest'
import { checkBuild, DEFAULT_RULES, finalAttrs, type Attrs } from './creationRules'
import { applyBuilt, applyImported } from './importCard'
import { computeSkills, freshSheet } from './useSheet'

const TABLE: Record<string, number> = { 会计: 5, 聆听: 20, 侦查: 25, 取悦: 15, 说服: 10, 神秘学: 5, 心理学: 10, '格斗(斗殴)': 25, 母语: 0, 闪避: 0, 信用评级: 0, 克苏鲁神话: 0 }
const BARTENDER = {
  name: '酒保', category: '服务业', credit_min: 8, credit_max: 25, skill_formula: 'EDU*2+APP*2',
  skills: ['会计', '格斗(斗殴)', '聆听', '心理学', '侦查'],
  groups: [{ label: '两项社交技能', pick: 2, options: ['取悦', '话术', '恐吓', '说服'] }], choices: 1,
}
const BASE: Attrs = { STR: 50, CON: 60, SIZ: 60, DEX: 50, APP: 60, INT: 70, POW: 60, EDU: 70 }

describe('AI 建卡结果载入人物卡', () => {
  it('带建卡记录：载成正常掷骰车卡，清单无违规', () => {
    const final = { ...BASE, STR: 45, APP: 55, EDU: 74 }
    const data = {
      name: '阿尔文', age: 45, base_attributes: final,
      skills: { ...TABLE, 母语: 74, 闪避: 25, 会计: 45, 取悦: 45, 说服: 40, 神秘学: 25, 信用评级: 20 },
      system_data: { occupation: '酒保', significantPeople: '妹妹' },
      creation: { method: 'roll' as const, base_attributes: BASE, age: 45, age_deductions: { STR: 5 }, occupation: '酒保', occupation_picks: ['取悦', '说服', '神秘学'] },
    }
    const s = applyBuilt(freshSheet(), data, TABLE, {})
    expect([s.method, s.imported, s.occName, s.raises['会计'], s.credit]).toEqual(['roll', null, '酒保', 40, 20])
    expect(s.story.significantPeople).toBe('妹妹')
    const build = {
      method: s.method, baseAttrs: s.rollSets[0], age: s.age, deductions: s.deductions, eduRolls: s.eduRolls,
      occ: BARTENDER, picks: s.picks, skills: {}, quickBoosts: [],
    }
    const attrs = finalAttrs(build)!
    expect(attrs).toEqual(final)
    build.skills = computeSkills(s, attrs, BARTENDER, TABLE, {})
    expect(checkBuild(build, DEFAULT_RULES, TABLE, {}).problems).toEqual([])
  })

  it('不带建卡记录：按现成卡处理', () => {
    const s = applyImported(freshSheet(), { name: '老卡', base_attributes: BASE, skills: { 侦查: 80 } })
    expect(s.imported?.skills['侦查']).toBe(80)
  })
})
