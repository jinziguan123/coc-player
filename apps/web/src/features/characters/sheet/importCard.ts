// 导入的 Excel 角色卡 / AI 填好的卡 → 人物卡状态。现成卡不按建卡规则约束（见 ImportedCard）。
import { normalizeWeapon } from '@/components/character/useCocData'
import { ageBand, ATTR_KEYS, CREDIT, skillStart, type Attrs, type AttrKey, type SpecTable } from './creationRules'
import type { SheetState, Story } from './useSheet'

/** 后端（AI 建卡）给的建卡记录，与 CreationRecord 同构。 */
export interface CreationRecordData {
  method: 'roll' | 'buy' | 'quick'
  base_attributes: Record<string, number>
  age: number
  age_deductions?: Record<string, number>
  occupation: string
  occupation_picks?: string[]
  quick_boosts?: string[]
}

export interface ImportedCharacterData {
  creation?: CreationRecordData | null
  name?: string
  age?: number
  base_attributes?: Record<string, number>
  skills?: Record<string, number>
  backstory?: string
  equipment?: string[]
  weapons?: Record<string, unknown>[]
  system_data?: Partial<Story> & {
    gender?: string
    residence?: string
    birthplace?: string
    creditRating?: number
    luck?: number
    occupation?: string
  }
}

/**
 * AI 按建卡规则建好的卡（带建卡记录）：载成一次正常的掷骰车卡——属性、年龄扣点、本职勾选、
 * 加点都摆在人物卡上，清单照常检查，玩家改哪儿都按规则算。不带记录的仍按现成卡处理。
 */
export function applyBuilt(
  s: SheetState, data: ImportedCharacterData, table: Record<string, number>, specs: SpecTable,
): SheetState {
  const rec = data.creation
  if (!rec || rec.method !== 'roll') return applyImported(s, data)
  const base = rec.base_attributes as Attrs
  const final = { ...base, ...(data.base_attributes ?? {}) } as Attrs
  const band = ageBand(rec.age)
  const raises: Record<string, number> = {}
  for (const [name, value] of Object.entries(data.skills ?? {})) {
    if (name === CREDIT) continue
    const spent = value - skillStart(name, final, table, specs)
    if (spent > 0) raises[name] = spent
  }
  const withStory = applyImported({ ...s, imported: null }, { ...data, base_attributes: undefined })
  return {
    ...withStory,
    method: 'roll',
    rollSets: [base, ...s.rollSets.slice(0, 2)],
    setIdx: 0,
    age: rec.age,
    deductions: (rec.age_deductions ?? {}) as Partial<Record<AttrKey, number>>,
    // 教育增强已由后端掷过：记成一次「已掷」的结果（roll=0），界面据此说明而不是重掷
    eduRolls: band.eduChecks ? [{ roll: 0, gain: final.EDU - base.EDU, after: final.EDU }] : [],
    occName: rec.occupation,
    picks: rec.occupation_picks ?? [],
    raises,
    credit: data.skills?.[CREDIT] ?? data.system_data?.creditRating ?? 0,
    quickValues: {},
    quickBoosts: [],
    imported: null,
  }
}

export function applyImported(s: SheetState, data: ImportedCharacterData): SheetState {
  const sd = data.system_data ?? {}
  const story = { ...s.story }
  for (const k of Object.keys(story) as (keyof Story)[]) {
    const v = sd[k]
    if (typeof v === 'string' && v) story[k] = v
  }
  // 没有结构化背景、只有一段自由文本：放进个人描述
  if (!Object.values(story).some(Boolean) && data.backstory) story.personalDescription = data.backstory

  const attrsIn = data.base_attributes ?? {}
  const hasAttrs = ATTR_KEYS.every((k) => typeof attrsIn[k] === 'number')
  const skills = { ...(data.skills ?? {}) }
  if (sd.creditRating != null && skills[CREDIT] == null) skills[CREDIT] = sd.creditRating

  return {
    ...s,
    name: data.name || s.name,
    age: data.age || s.age,
    gender: sd.gender || s.gender,
    residence: sd.residence || s.residence,
    birthplace: sd.birthplace || s.birthplace,
    luck: sd.luck || s.luck,
    story,
    equipText: data.equipment?.length ? data.equipment.join('、') : s.equipText,
    weapons: data.weapons?.length ? data.weapons.map(normalizeWeapon) : s.weapons,
    imported: hasAttrs ? { attrs: attrsIn as Attrs, skills, occupation: sd.occupation } : s.imported,
    occName: sd.occupation || s.occName,
  }
}
