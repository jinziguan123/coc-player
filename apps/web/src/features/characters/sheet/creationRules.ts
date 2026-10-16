/**
 * 建卡规则（CoC 7 版）——`server/app/rules/coc/components/creation.py` 的前端镜像。
 *
 * 界面要随手即时反馈（清单打勾、点数剩余、超限标红），每改一下走一次网络太黏，所以在前端
 * 再算一遍；但**创建时以后端校验为准**——后端用同一套规则拒掉不合规的卡。两边的口径由
 * creationRules.test.ts 与 server/tests/test_character_creation.py 各自钉住同样的用例。
 */

export const ATTR_KEYS = ['STR', 'CON', 'SIZ', 'DEX', 'APP', 'INT', 'POW', 'EDU'] as const
export type AttrKey = (typeof ATTR_KEYS)[number]
export type Attrs = Record<AttrKey, number>
export const PLUS_SIX: ReadonlySet<string> = new Set(['SIZ', 'INT', 'EDU'])

export const ATTR_LABELS: Record<AttrKey, string> = {
  STR: '力量', CON: '体质', SIZ: '体型', DEX: '敏捷', APP: '外貌', INT: '智力', POW: '意志', EDU: '教育',
}

export type Method = 'roll' | 'buy' | 'quick'
export const CREDIT = '信用评级'
export const LOCKED_SKILLS: ReadonlySet<string> = new Set(['克苏鲁神话'])

export interface OccupationGroup { label: string; pick: number; options: string[] }
export interface Occupation {
  name: string
  category: string
  credit_min: number
  credit_max: number
  skill_formula: string
  skills: string[]
  groups: OccupationGroup[]
  choices: number
}

export interface CreationRules {
  skill_cap_occupation: number
  skill_cap_other: number
  point_buy_total: number
  quick_attrs: number[]
  quick_skills: number[]
  quick_boosts: number
  quick_boost: number
}

export const DEFAULT_RULES: CreationRules = {
  skill_cap_occupation: 90, skill_cap_other: 90, point_buy_total: 460,
  quick_attrs: [40, 50, 50, 50, 60, 60, 70, 80], quick_skills: [70, 60, 60, 50, 50, 50, 40, 40, 40],
  quick_boosts: 4, quick_boost: 20,
}

// ── 骰子 ──────────────────────────────────────────────────────────────

const d = (n: number) => 1 + Math.floor(Math.random() * n)
export const rollAttr = (k: AttrKey) => (PLUS_SIX.has(k) ? d(6) + d(6) + 6 : d(6) + d(6) + d(6)) * 5
export const rollAttrSet = (): Attrs => Object.fromEntries(ATTR_KEYS.map((k) => [k, rollAttr(k)])) as Attrs
/** 幸运 3D6×5；少年（≤19）掷两次取高。 */
export function rollLuck(age: number): number {
  const once = () => (d(6) + d(6) + d(6)) * 5
  return age <= 19 ? Math.max(once(), once()) : once()
}

// ── 年龄 ──────────────────────────────────────────────────────────────

export interface AgeBand {
  eduChecks: number
  deductFrom: AttrKey[]
  deduct: number
  app: number
  eduPenalty: number
  mov: number
  /** 给玩家看的规则条目 */
  text: string[]
}

const BANDS: ReadonlyArray<[number, number, number, number, number]> = [
  // [年龄上限, 教育增强次数, 体能合计减值, 外貌减值, 移动力减值]
  [39, 1, 0, 0, 0], [49, 2, 5, 5, 1], [59, 3, 10, 10, 2], [69, 4, 20, 15, 3], [79, 4, 40, 20, 4], [89, 4, 80, 25, 5],
]

export function ageBand(age: number): AgeBand {
  if (age <= 19) {
    return {
      eduChecks: 0, deductFrom: ['STR', 'SIZ'], deduct: 5, app: 0, eduPenalty: 5, mov: 0,
      text: ['力量和体型合计扣 5 点（自己分配）', '教育 −5', '幸运掷两次取高'],
    }
  }
  const [, checks, physical, app, mov] = BANDS.find((b) => age <= b[0]) ?? BANDS[BANDS.length - 1]
  const text = [`教育增强检定 ${checks} 次`]
  if (physical) text.push(`力量、体质、敏捷合计扣 ${physical} 点（自己分配）`)
  if (app) text.push(`外貌 −${app}`)
  if (mov) text.push(`移动力 −${mov}`)
  return {
    eduChecks: checks, deductFrom: physical ? ['STR', 'CON', 'DEX'] : [], deduct: physical,
    app, eduPenalty: 0, mov, text,
  }
}

export interface EduRoll { roll: number; gain: number; after: number }

/** 教育增强检定：每次 D100 > 当前教育则 +1D10，封顶 99。 */
export function rollEduChecks(edu: number, times: number): EduRoll[] {
  const out: EduRoll[] = []
  let cur = edu
  for (let i = 0; i < times; i++) {
    const r = d(100)
    const gain = r > cur ? d(10) : 0
    cur = Math.min(99, cur + gain)
    out.push({ roll: r, gain, after: cur })
  }
  return out
}

export function deductLeft(age: number, deductions: Partial<Record<AttrKey, number>>): number {
  const band = ageBand(age)
  return band.deduct - band.deductFrom.reduce((s, k) => s + (deductions[k] ?? 0), 0)
}

/** 年龄修正后的属性：扣点、外貌减值、少年教育 −5，教育取增强检定的结果。 */
export function applyAge(
  base: Attrs, age: number, deductions: Partial<Record<AttrKey, number>>, eduRolls: EduRoll[],
): Attrs {
  const band = ageBand(age)
  const out = { ...base }
  for (const k of band.deductFrom) out[k] = Math.max(1, out[k] - (deductions[k] ?? 0))
  out.APP = Math.max(1, out.APP - band.app)
  out.EDU = eduRolls.length ? eduRolls[eduRolls.length - 1].after : base.EDU - band.eduPenalty
  return out
}

// ── 职业 ──────────────────────────────────────────────────────────────

export const skillBase = (s: string) => s.split(/[(（]/)[0]

const matches = (listed: string, skill: string) =>
  listed === skill || (listed === skillBase(listed) && skillBase(skill) === listed)

/** `fixed` 固定本职 / `group:i` 第 i 个选择组的候选 / `free` 其他。 */
export function skillRole(occ: Occupation, skill: string): string {
  if (occ.skills.some((s) => matches(s, skill))) return 'fixed'
  const i = occ.groups.findIndex((g) => g.options.some((o) => matches(o, skill)))
  return i >= 0 ? `group:${i}` : 'free'
}

export function isOccSkill(occ: Occupation | null, picks: string[], skill: string): boolean {
  if (!occ) return false
  if (skill === CREDIT) return true
  return skillRole(occ, skill) === 'fixed' || picks.includes(skill)
}

export function pickCounts(occ: Occupation, picks: string[]): { groups: number[]; free: number } {
  const groups = occ.groups.map(() => 0)
  let free = 0
  for (const p of picks) {
    const role = skillRole(occ, p)
    if (role === 'free') free++
    else if (role.startsWith('group:')) groups[Number(role.slice(6))]++
  }
  return { groups, free }
}

/** 这项技能现在还能不能再勾（所属的组 / 任意 N 项已经满了就不行）。 */
export function canPick(occ: Occupation, picks: string[], skill: string): boolean {
  const role = skillRole(occ, skill)
  if (role === 'fixed' || picks.includes(skill)) return false
  const c = pickCounts(occ, picks)
  if (role === 'free') return c.free < occ.choices
  const i = Number(role.slice(6))
  return c.groups[i] < occ.groups[i].pick
}

/** 职业技能点公式：属性*倍数 用 + 相加，| 取较高者（如 EDU*2+STR|DEX*2）。 */
export function occupationPoints(formula: string, attrs: Attrs): number {
  return formula.split('+').reduce((sum, part) => {
    const [keys, mult] = part.trim().split('*')
    const best = Math.max(...keys.split('|').map((k) => attrs[k.trim() as AttrKey] ?? 0))
    return sum + best * Number(mult || 0)
  }, 0)
}

export function formulaText(formula: string): string {
  return formula.split('+').map((part) => {
    const [keys, mult] = part.trim().split('*')
    return `${keys.split('|').map((k) => ATTR_LABELS[k as AttrKey] ?? k).join('或')}×${mult}`
  }).join(' + ')
}

// ── 技能 ──────────────────────────────────────────────────────────────

export interface SpecTable { [category: string]: { base_init: number; items: { name: string; init: number }[] } }

/** 技能未加点时的起始值：母语=EDU、闪避=DEX/2、专精按专精表、其余按技能表。 */
export function skillStart(skill: string, attrs: Attrs, table: Record<string, number>, specs: SpecTable): number {
  const base = skillBase(skill)
  if (base === '母语') return attrs.EDU
  if (skill === '闪避') return Math.floor(attrs.DEX / 2)
  if (skill in table) return table[skill]
  const spec = specs[base]
  if (spec) {
    const inner = skill.slice(base.length).replace(/^[(（]|[)）]$/g, '')
    return spec.items.find((i) => i.name === inner)?.init ?? spec.base_init
  }
  return table[base] ?? 0
}

// ── 校验 ──────────────────────────────────────────────────────────────

export type Area = 'attr' | 'age' | 'occ' | 'skill'
export interface Issue { area: Area; text: string }

export interface BuildState {
  method: Method
  baseAttrs: Partial<Attrs>
  age: number
  deductions: Partial<Record<AttrKey, number>>
  eduRolls: EduRoll[]
  occ: Occupation | null
  picks: string[]
  /** 技能最终值（含起始值）。信用评级直接是值。 */
  skills: Record<string, number>
  quickBoosts: string[]
}

export function isComplete(a: Partial<Attrs>): a is Attrs {
  return ATTR_KEYS.every((k) => typeof a[k] === 'number')
}

export function finalAttrs(s: BuildState): Attrs | null {
  return isComplete(s.baseAttrs) ? applyAge(s.baseAttrs, s.age, s.deductions, s.eduRolls) : null
}

/** 违规（禁止创建）与提醒（可以创建）。规则与后端 validate_build 一一对应。 */
export function checkBuild(
  s: BuildState, rules: CreationRules, table: Record<string, number>, specs: SpecTable,
): { problems: Issue[]; warnings: Issue[] } {
  const problems: Issue[] = []
  const warnings: Issue[] = []
  const P = (area: Area, text: string) => problems.push({ area, text })
  const W = (area: Area, text: string) => warnings.push({ area, text })

  if (!isComplete(s.baseAttrs)) {
    P('attr', s.method === 'quick' ? '还有属性没放值' : '属性还没定完')
  } else if (s.method === 'quick') {
    const got = ATTR_KEYS.map((k) => s.baseAttrs[k] as number).sort((a, b) => a - b)
    if (got.join() !== [...rules.quick_attrs].sort((a, b) => a - b).join()) P('attr', '快速车卡的八个值要各用一次')
  } else if (s.method === 'buy') {
    const used = ATTR_KEYS.reduce((t, k) => t + (s.baseAttrs[k] as number), 0)
    if (used > rules.point_buy_total) P('attr', `点数购买超出 ${used - rules.point_buy_total} 点`)
    else if (used < rules.point_buy_total) W('attr', `点数购买还剩 ${rules.point_buy_total - used} 点`)
  }
  const left = deductLeft(s.age, s.deductions)
  if (left > 0) P('age', `年龄扣点还差 ${left} 点没分配`)
  if (left < 0) P('age', `年龄扣点多分配了 ${-left} 点`)

  const a = finalAttrs(s)
  if (!s.occ) { P('occ', '还没选职业'); return { problems, warnings } }
  const occ = s.occ
  const c = pickCounts(occ, s.picks)
  occ.groups.forEach((g, i) => { if (c.groups[i] !== g.pick) P('occ', `${g.label}：还需选 ${g.pick - c.groups[i]} 项`) })
  if (c.free !== occ.choices) P('occ', `任意其他技能：还需选 ${occ.choices - c.free} 项`)

  const credit = s.skills[CREDIT] ?? 0
  if (credit < occ.credit_min || credit > occ.credit_max) {
    P('skill', `信用评级须在 ${occ.credit_min}–${occ.credit_max}`)
  }
  if (!a) return { problems, warnings }

  const isOcc = (k: string) => isOccSkill(occ, s.picks, k)
  const alloc: Record<string, number> = {}
  for (const [name, value] of Object.entries(s.skills)) {
    if (name === CREDIT) continue
    const start = skillStart(name, a, table, specs)
    const spent = value - start
    if (spent < 0) P('skill', `${name} 低于起始值 ${start}`)
    else if (spent && LOCKED_SKILLS.has(name)) P('skill', `${name} 建卡时不能加点`)
    else if (spent) {
      alloc[name] = spent
      const cap = isOcc(name) ? rules.skill_cap_occupation : rules.skill_cap_other
      if (value > Math.max(cap, start)) P('skill', `${name} 超过建卡上限 ${cap}`)
    }
  }

  if (s.method === 'quick') {
    const used: number[] = []
    for (const [name, spent] of Object.entries(alloc)) {
      if (isOcc(name)) used.push(s.skills[name])
      else if (s.quickBoosts.includes(name)) { if (spent !== rules.quick_boost) P('skill', `${name} 应为起始值 +${rules.quick_boost}`) }
      else P('skill', `${name} 不能加点`)
    }
    const pool = [...rules.quick_skills]
    for (const v of used) {
      const i = pool.indexOf(v)
      if (i < 0) { P('skill', `本职技能的值只能从 ${rules.quick_skills.join('/')} 里各取一次`); break }
      pool.splice(i, 1)
    }
    if (pool.length) W('skill', `本职定值还有 ${pool.length} 个没用`)
    if (s.quickBoosts.length < rules.quick_boosts) W('skill', `兴趣 +${rules.quick_boost} 还差 ${rules.quick_boosts - s.quickBoosts.length} 项`)
    return { problems, warnings }
  }

  const occPts = occupationPoints(occ.skill_formula, a)
  const intPts = a.INT * 2
  const spentOther = Object.entries(alloc).filter(([k]) => !isOcc(k)).reduce((t, [, v]) => t + v, 0)
  const spentOcc = Object.entries(alloc).filter(([k]) => isOcc(k)).reduce((t, [, v]) => t + v, 0)
  if (credit > occPts) P('skill', `信用评级超过职业点 ${occPts}`)
  if (spentOther > intPts) P('skill', `兴趣点超出 ${spentOther - intPts}`)
  else if (spentOcc + credit > occPts + (intPts - spentOther)) P('skill', `职业点超出 ${spentOcc + credit - occPts - (intPts - spentOther)}`)
  else {
    const occLeft = occPts - spentOcc - credit
    const intLeft = intPts - spentOther + Math.min(0, occLeft)
    if (occLeft > 0) W('skill', `还有 ${occLeft} 职业点没用`)
    if (intLeft > 0) W('skill', `还有 ${intLeft} 兴趣点没用`)
  }
  return { problems, warnings }
}

/** 点数面板：职业点 / 兴趣点各用了多少（本职技能先扣职业点，不够再扣兴趣点）。 */
export function pointUsage(s: BuildState, table: Record<string, number>, specs: SpecTable) {
  const a = finalAttrs(s)
  if (!a || !s.occ) return null
  const occ = s.occ
  const occTotal = occupationPoints(occ.skill_formula, a)
  const intTotal = a.INT * 2
  let occSpent = s.skills[CREDIT] ?? 0
  let intSpent = 0
  for (const [name, value] of Object.entries(s.skills)) {
    if (name === CREDIT) continue
    const spent = Math.max(0, value - skillStart(name, a, table, specs))
    if (isOccSkill(occ, s.picks, name)) occSpent += spent
    else intSpent += spent
  }
  const overflow = Math.max(0, occSpent - occTotal)
  return { occTotal, intTotal, occUsed: occSpent - overflow, intUsed: intSpent + overflow }
}

/** 提交给后端的建卡记录（与 CreationRecord 同构）。 */
export function creationRecord(s: BuildState) {
  return {
    method: s.method,
    base_attributes: s.baseAttrs,
    age: s.age,
    age_deductions: Object.fromEntries(Object.entries(s.deductions).filter(([, v]) => v)),
    occupation: s.occ?.name ?? '',
    occupation_picks: s.picks,
    quick_boosts: s.quickBoosts,
  }
}
