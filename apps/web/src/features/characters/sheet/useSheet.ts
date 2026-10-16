/**
 * 一页式人物卡的状态：一个 reducer 管全部字段，草稿自动存 localStorage。
 *
 * 加点模式下存的是「每项技能加了多少点」而不是最终值——属性一变（比如年龄修正改了教育），
 * 母语、闪避的起始值自动跟着变，已加的点不会被吞或错位。快速车卡存的是本职定值与 +20 的四项。
 */
import { useEffect, useMemo, useReducer, useState } from 'react'
import { api } from '@/api/client'
import { ruleQuery, useSpecializations } from '@/components/character/useCocData'
import type { CharWeapon } from '@/components/character/useCocData'
import type { AssetsInfo, ModuleExperience, Mythos, Relation } from '@/components/character/CharacterExtraEditors'
import {
  ageBand, ATTR_KEYS, checkBuild, CREDIT, DEFAULT_RULES, finalAttrs, isOccSkill, rollAttrSet, rollEduChecks,
  rollLuck, skillStart,
  type Attrs, type AttrKey, type BuildState, type CreationRules, type EduRoll, type Method, type Occupation, type SpecTable,
} from './creationRules'

export const DRAFT_KEY = 'trpg_character_sheet_draft'

export interface Story {
  personalDescription: string
  ideologyBeliefs: string
  significantPeople: string
  meaningfulLocations: string
  treasuredPossessions: string
  traits: string
  scarsAndWounds: string
  phobiasAndManias: string
  investigatorHistory: string
}
export const STORY_FIELDS: ReadonlyArray<[keyof Story, string, boolean]> = [
  ['personalDescription', '个人描述', false], ['ideologyBeliefs', '思想与信念', false],
  ['significantPeople', '重要之人', true], ['meaningfulLocations', '意义非凡之地', false],
  ['treasuredPossessions', '宝贵之物', false], ['traits', '特质', false],
  ['scarsAndWounds', '伤口与疤痕', false], ['phobiasAndManias', '恐惧症与狂躁症', false],
  ['investigatorHistory', '调查员经历', false],
]

/** 导入 / AI 填表进来的现成卡：不按建卡规则约束，照原样建。 */
export interface ImportedCard { attrs: Attrs; skills: Record<string, number>; occupation?: string }

export interface SheetState {
  name: string
  moduleId: string
  gender: string
  residence: string
  birthplace: string
  method: Method
  age: number
  rollSets: Attrs[]
  setIdx: number
  buy: Attrs
  quick: Partial<Attrs>
  deductions: Partial<Record<AttrKey, number>>
  eduRolls: EduRoll[]
  luck: number
  occName: string | null
  picks: string[]
  /** 掷骰 / 点数购买：每项技能加了多少点 */
  raises: Record<string, number>
  credit: number
  /** 快速车卡：本职技能 → 定值 */
  quickValues: Record<string, number>
  quickBoosts: string[]
  /** 经专精弹窗添加的「基名(专精)」技能 */
  extraSkills: string[]
  imported: ImportedCard | null
  story: Story
  assets: AssetsInfo
  assetsTouched: boolean
  weapons: CharWeapon[]
  equipText: string
  mythos: Mythos
  relations: Relation[]
  moduleHistory: ModuleExperience[]
}

const EMPTY_STORY: Story = {
  personalDescription: '', ideologyBeliefs: '', significantPeople: '', meaningfulLocations: '',
  treasuredPossessions: '', traits: '', scarsAndWounds: '', phobiasAndManias: '', investigatorHistory: '',
}
const BUY_START: Attrs = { STR: 50, CON: 60, SIZ: 60, DEX: 60, APP: 50, INT: 70, POW: 60, EDU: 50 }

export function freshSheet(): SheetState {
  const rollSets = [rollAttrSet(), rollAttrSet(), rollAttrSet()]
  const s: SheetState = {
    name: '', moduleId: '', gender: '', residence: '', birthplace: '',
    method: 'roll', age: 25, rollSets, setIdx: 0, buy: { ...BUY_START }, quick: {},
    deductions: {}, eduRolls: [], luck: rollLuck(25),
    occName: null, picks: [], raises: {}, credit: 0, quickValues: {}, quickBoosts: [], extraSkills: [],
    imported: null, story: { ...EMPTY_STORY },
    assets: { cash: 0, spendingLevel: 0, assets: '' }, assetsTouched: false,
    weapons: [], equipText: '', mythos: { spells: [], tomes: [], encounters: [] }, relations: [], moduleHistory: [],
  }
  return reroll(s)
}

export function baseAttrsOf(s: SheetState): Partial<Attrs> {
  if (s.imported) return s.imported.attrs
  if (s.method === 'roll') return s.rollSets[s.setIdx] ?? {}
  if (s.method === 'buy') return s.buy
  return s.quick
}

/** 属性或年龄一变，教育增强检定按新的教育与年龄段重掷（规则书：每次掷 D100 对比当前教育）。 */
function reroll(s: SheetState): SheetState {
  const base = baseAttrsOf(s)
  const band = ageBand(s.age)
  const eduRolls = typeof base.EDU === 'number' && band.eduChecks
    ? rollEduChecks(base.EDU - band.eduPenalty, band.eduChecks) : []
  return { ...s, eduRolls }
}

export type SheetAction =
  | { type: 'set'; patch: Partial<SheetState> }
  | { type: 'method'; method: Method }
  | { type: 'reroll' }
  | { type: 'pickSet'; idx: number }
  | { type: 'buy'; key: AttrKey; value: number }
  | { type: 'quickAttr'; key: AttrKey; value: number | null }
  | { type: 'age'; age: number }
  | { type: 'deduct'; key: AttrKey; delta: number }
  | { type: 'occupation'; occ: Occupation }
  | { type: 'togglePick'; skill: string }
  | { type: 'raise'; skill: string; value: number }
  | { type: 'credit'; value: number }
  | { type: 'quickValue'; skill: string; value: number | null }
  | { type: 'toggleBoost'; skill: string; max: number }
  | { type: 'addSkill'; skill: string }
  | { type: 'story'; key: keyof Story; value: string }
  | { type: 'load'; state: SheetState }

export function sheetReducer(s: SheetState, a: SheetAction): SheetState {
  switch (a.type) {
    case 'set': return { ...s, ...a.patch }
    case 'method': return reroll({ ...s, method: a.method, imported: null, raises: {}, quickValues: {}, quickBoosts: [] })
    case 'reroll': return reroll({ ...s, rollSets: [rollAttrSet(), rollAttrSet(), rollAttrSet()], setIdx: 0, luck: rollLuck(s.age) })
    case 'pickSet': return reroll({ ...s, setIdx: a.idx })
    case 'buy': return reroll({ ...s, buy: { ...s.buy, [a.key]: a.value } })
    case 'quickAttr': {
      const quick = { ...s.quick }
      if (a.value == null) delete quick[a.key]
      else quick[a.key] = a.value
      return reroll({ ...s, quick })
    }
    case 'age': {
      const young = a.age <= 19
      return reroll({ ...s, age: a.age, deductions: {}, luck: young !== s.age <= 19 ? rollLuck(a.age) : s.luck })
    }
    case 'deduct': {
      const band = ageBand(s.age)
      const cur = s.deductions[a.key] ?? 0
      const used = band.deductFrom.reduce((t, k) => t + (s.deductions[k] ?? 0), 0)
      const next = Math.max(0, cur + a.delta)
      if (a.delta > 0 && used >= band.deduct) return s
      return { ...s, deductions: { ...s.deductions, [a.key]: Math.min(next, cur + band.deduct - used) } }
    }
    case 'occupation':
      return { ...s, occName: a.occ.name, picks: [], quickValues: {}, credit: a.occ.credit_min }
    case 'togglePick': {
      if (s.picks.includes(a.skill)) {
        const { [a.skill]: _r, ...raises } = s.raises
        const { [a.skill]: _q, ...quickValues } = s.quickValues
        return { ...s, picks: s.picks.filter((p) => p !== a.skill), raises, quickValues }
      }
      return { ...s, picks: [...s.picks, a.skill], quickBoosts: s.quickBoosts.filter((b) => b !== a.skill) }
    }
    case 'raise': return { ...s, raises: { ...s.raises, [a.skill]: Math.max(0, a.value) } }
    case 'credit': return { ...s, credit: Math.max(0, Math.min(99, a.value)) }
    case 'quickValue': {
      const quickValues = { ...s.quickValues }
      if (a.value == null) delete quickValues[a.skill]
      else quickValues[a.skill] = a.value
      return { ...s, quickValues }
    }
    case 'toggleBoost': {
      if (s.quickBoosts.includes(a.skill)) return { ...s, quickBoosts: s.quickBoosts.filter((b) => b !== a.skill) }
      return s.quickBoosts.length >= a.max ? s : { ...s, quickBoosts: [...s.quickBoosts, a.skill] }
    }
    case 'addSkill': return s.extraSkills.includes(a.skill) ? s : { ...s, extraSkills: [...s.extraSkills, a.skill] }
    case 'story': return { ...s, story: { ...s.story, [a.key]: a.value } }
    case 'load': return a.state
  }
}

export function readDraft(): { savedAt: string; state: SheetState } | null {
  try {
    const raw = localStorage.getItem(DRAFT_KEY)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

/** 页面要的全部数据与派生结果。 */
export function useSheet(initial?: SheetState) {
  const [state, dispatch] = useReducer(sheetReducer, initial ?? null, (init) => init ?? freshSheet())
  const moduleId = state.moduleId || null
  const [occupations, setOccupations] = useState<Occupation[]>([])
  const [categories, setCategories] = useState<string[]>([])
  const [table, setTable] = useState<Record<string, number>>({})
  const [rules, setRules] = useState<CreationRules>(DEFAULT_RULES)
  const specData = useSpecializations(moduleId)
  const specs: SpecTable = useMemo(() => specData?.categories ?? {}, [specData])

  useEffect(() => {
    const q = ruleQuery(moduleId)
    api.get<Occupation[]>(`/rules/coc/occupations${q}`).then(setOccupations).catch(() => {})
    api.get<string[]>(`/rules/coc/occupation-categories${q}`).then(setCategories).catch(() => {})
    api.get<{ default_skills: Record<string, number> }>(`/rules/coc/character-schema${q}`)
      .then((r) => setTable(r.default_skills || {})).catch(() => {})
    api.get<CreationRules>(`/rules/coc/creation-rules${q}`).then(setRules).catch(() => {})
  }, [moduleId])

  // 草稿：每次改动 600ms 后存一次
  const [savedAt, setSavedAt] = useState<string | null>(null)
  useEffect(() => {
    const t = setTimeout(() => {
      try {
        const at = new Date().toISOString()
        localStorage.setItem(DRAFT_KEY, JSON.stringify({ savedAt: at, state }))
        setSavedAt(at)
      } catch { /* 存不下就算了，不打断建卡 */ }
    }, 600)
    return () => clearTimeout(t)
  }, [state])

  const occ = occupations.find((o) => o.name === state.occName) ?? null
  const build: BuildState = useMemo(() => {
    const baseAttrs = baseAttrsOf(state)
    const draft: BuildState = {
      method: state.method, baseAttrs, age: state.age, deductions: state.deductions, eduRolls: state.eduRolls,
      occ, picks: state.picks, skills: {}, quickBoosts: state.quickBoosts,
    }
    const attrs = finalAttrs(draft)
    draft.skills = attrs ? computeSkills(state, attrs, occ, table, specs) : {}
    return draft
  }, [state, occ, table, specs])

  const attrs = state.imported ? state.imported.attrs : finalAttrs(build)
  const check = useMemo(
    () => (state.imported ? { problems: [], warnings: [] } : checkBuild(build, rules, table, specs)),
    [state.imported, build, rules, table, specs],
  )
  const skillNames = useMemo(() => {
    const names = new Set([...Object.keys(table), ...state.extraSkills, ...Object.keys(state.imported?.skills ?? {})])
    return [...names]
  }, [table, state.extraSkills, state.imported])

  return {
    state, dispatch, occupations, categories, table, rules, specs, occ, build, attrs, check, skillNames, savedAt,
    skillValue: (name: string) => (state.imported ? state.imported.skills[name] ?? table[name] ?? 0 : build.skills[name] ?? 0),
    skillStartOf: (name: string) => (attrs ? skillStart(name, attrs, table, specs) : table[name] ?? 0),
    isOcc: (name: string) => isOccSkill(occ, state.picks, name),
  }
}

/** 由加点 / 快速车卡定值算出每项技能的最终值。 */
export function computeSkills(
  s: SheetState, attrs: Attrs, occ: Occupation | null, table: Record<string, number>, specs: SpecTable,
): Record<string, number> {
  const out: Record<string, number> = {}
  for (const name of new Set([...Object.keys(table), ...s.extraSkills])) {
    if (name === CREDIT) continue
    const start = skillStart(name, attrs, table, specs)
    if (s.method === 'quick') {
      const assigned = isOccSkill(occ, s.picks, name) ? s.quickValues[name] : undefined
      out[name] = assigned != null ? Math.max(assigned, start) : start + (s.quickBoosts.includes(name) ? 20 : 0)
    } else {
      out[name] = start + (s.raises[name] ?? 0)
    }
  }
  out[CREDIT] = s.credit
  return out
}

export const attrKeys = ATTR_KEYS
