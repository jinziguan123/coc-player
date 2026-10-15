import { createContext, useContext, useEffect, useState } from 'react'
import { api } from '../../api/client'

export interface SpecItem { name: string; init: number }
export interface SpecCategory { base_init: number; items: SpecItem[] }
export interface Specializations {
  categories: Record<string, SpecCategory>
  single: string[]   // 唯一专精（母语），值固定为 EDU
}

export interface WeaponDef {
  name: string
  skill: string
  dam: string
  tho: number       // 0/1 是否贯穿
  range: string
  round: string     // 次数 a/b
  num: string       // 装弹量
  price: string
  err: string       // 故障率
  time: string
  category: string  // 大类：常规/手枪/半自动步枪/…/其他
}

export interface WeaponData {
  weapons: WeaponDef[]
  categories: string[]   // 大类展示顺序
}

// 角色携带的武器（规范字段；兼容历史 damage/attacks/ammo）
export interface CharWeapon {
  name: string
  skill?: string
  success?: number   // 成功率
  dam?: string       // 伤害
  range?: string     // 射程
  tho?: boolean      // 是否贯穿
  round?: string     // 次数 a/b
  num?: string       // 装弹量
  err?: string       // 故障
}

/** 历史武器对象（含旧字段 damage/attacks/ammo）归一化为规范字段。 */
export function normalizeWeapon(w: Record<string, unknown>): CharWeapon {
  return {
    name: String(w.name ?? ''),
    skill: (w.skill as string) ?? '',
    success: (w.success as number) ?? undefined,
    dam: (w.dam as string) ?? (w.damage as string) ?? '',
    range: (w.range as string) ?? '',
    tho: typeof w.tho === 'boolean' ? w.tho : w.tho === 1,
    round: (w.round as string) ?? (w.attacks != null ? String(w.attacks) : ''),
    num: (w.num as string) ?? (w.ammo as string) ?? '',
    err: (w.err as string) ?? '',
  }
}

// 规则资料按「这一桌的村规 + 当前模组自带的数据」取：房主或模组作者可以改武器、专精表。
// 建卡/编辑卡片时由页面用 RuleModuleContext 声明当前模组；没声明就只看村规。

/** 当前建卡/编卡所属的模组（决定是否叠上模组自带的数据表）。 */
export const RuleModuleContext = createContext<string | null>(null)

/** 规则资料接口的查询串：带上模组时叠加模组自带的数据。 */
export function ruleQuery(moduleId?: string | null): string {
  return moduleId ? `?module_id=${encodeURIComponent(moduleId)}` : ''
}

// 进程内缓存，按模组分开；数据表编辑器保存后清空（见 invalidateRuleDataCache）。
const _specCache = new Map<string, Specializations>()
const _weaponCache = new Map<string, WeaponData>()

/** 数据表改动后调用：下次取专精/武器表时重新拉取。 */
export function invalidateRuleDataCache(): void {
  _specCache.clear()
  _weaponCache.clear()
}

/** 专精类别（母语/外语/格斗/射击/科学/生存/技艺/驾驶），按模组缓存。
 *  ``module`` 不传则取上层 RuleModuleContext 声明的模组。 */
export function useSpecializations(module?: string | null) {
  const ctx = useContext(RuleModuleContext)
  const moduleId = module !== undefined ? module : ctx
  const key = moduleId ?? ''
  const [data, setData] = useState<Specializations | null>(_specCache.get(key) ?? null)
  useEffect(() => {
    const hit = _specCache.get(key)
    if (hit) { setData(hit); return }
    api.get<Specializations>(`/rules/coc/specializations${ruleQuery(moduleId)}`)
      .then((d) => { _specCache.set(key, d); setData(d) })
      .catch(() => setData({ categories: {}, single: [] }))
  }, [key, moduleId])
  return data
}

/** 武器表 + 大类顺序（这一桌的村规 + 当前模组），按模组缓存。
 *  ``module`` 不传则取上层 RuleModuleContext 声明的模组。 */
export function useWeapons(module?: string | null) {
  const ctx = useContext(RuleModuleContext)
  const moduleId = module !== undefined ? module : ctx
  const key = moduleId ?? ''
  const [data, setData] = useState<WeaponData | null>(_weaponCache.get(key) ?? null)
  useEffect(() => {
    const hit = _weaponCache.get(key)
    if (hit) { setData(hit); return }
    api.get<WeaponData>(`/rules/coc/weapons${ruleQuery(moduleId)}`)
      .then((d) => { _weaponCache.set(key, d); setData(d) })
      .catch(() => setData({ weapons: [], categories: [] }))
  }, [key, moduleId])
  return data
}

export interface OccupationDef {
  name: string
  credit_min: number
  credit_max: number
  skill_formula: string
  skills: string[]
  choices: number
  category?: string
}

/** 职业表与大类顺序（这一桌的村规 + 当前模组）。模组一换就重取：模组可以带专属职业。 */
export function useOccupations(moduleId: string | null) {
  const [occupations, setOccupations] = useState<OccupationDef[]>([])
  const [categories, setCategories] = useState<string[]>([])
  useEffect(() => {
    const q = ruleQuery(moduleId)
    api.get<OccupationDef[]>(`/rules/coc/occupations${q}`).then(setOccupations).catch(() => {})
    api.get<string[]>(`/rules/coc/occupation-categories${q}`).then(setCategories).catch(() => {})
  }, [moduleId])
  return { occupations, categories }
}

// ---- 资产：按信用评级换算现金/消费水平/资产（CoC 7 版标准表，1920s 美元） ----
export interface DerivedAssets {
  tier: string          // 财富等级
  spendingLevel: number // 消费水平 $
  cash: number          // 现金 $
  assets: number        // 资产 $
}

export function assetTier(cr: number): string {
  if (cr <= 0) return '一贫如洗'
  if (cr <= 9) return '贫穷'
  if (cr <= 49) return '普通'
  if (cr <= 89) return '富裕'
  if (cr <= 98) return '富有'
  return '巨富'
}

/**
 * 现金列的倍率曾经是 ×2 / ×20 / ×50 / ×100，比规则书高一个量级
 * （消费水平与资产两列一直是对的，只有现金这一列抄错），
 * 建出来的调查员一上来就揣着十倍的钱。已存的角色卡不受影响——
 * 现金是建卡时算出来存进 system_data 的快照，这里只管新算的。
 */
export function deriveAssets(cr: number): DerivedAssets {
  const tier = assetTier(cr)
  if (cr <= 0) return { tier, spendingLevel: 0.5, cash: 0.5, assets: 0 }
  if (cr <= 9) return { tier, spendingLevel: 2, cash: cr * 1, assets: cr * 10 }
  if (cr <= 49) return { tier, spendingLevel: 10, cash: cr * 2, assets: cr * 50 }
  if (cr <= 89) return { tier, spendingLevel: 50, cash: cr * 5, assets: cr * 500 }
  if (cr <= 98) return { tier, spendingLevel: 250, cash: cr * 20, assets: cr * 2000 }
  return { tier, spendingLevel: 5000, cash: 50000, assets: 5000000 }
}

/** 把技能键拆成基名与专精，如 "格斗(斗殴)" → ["格斗","斗殴"]；无专精则 spec 为空。 */
export function splitSkill(key: string): [string, string] {
  const m = key.match(/^(.+?)\((.+)\)$/)
  return m ? [m[1], m[2]] : [key, '']
}
