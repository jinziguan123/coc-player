// 导入的 Excel 角色卡 / AI 填好的卡 → 人物卡状态。现成卡不按建卡规则约束（见 ImportedCard）。
import { normalizeWeapon } from '@/components/character/useCocData'
import { ATTR_KEYS, CREDIT, type Attrs } from './creationRules'
import type { SheetState, Story } from './useSheet'

export interface ImportedCharacterData {
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
