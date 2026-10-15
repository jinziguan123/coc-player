// 规则页的草稿逻辑：分区、与原文/已保存的差异、数据表行的状态。全是纯函数，可单测。
//
// 三份值的关系：
// - 原文（RAW）：参数 schema 的默认值 / 官方数据表；
// - 已保存：后端存着的这一桌规则；
// - 草稿：页面上正在改的。
// 「改过」= 与原文不同（概览与分区上的空心数字）；「未保存」= 草稿与已保存不同（实心数字、草稿条）。
import {
  componentFields, isShown,
  type RuleComponent, type RuleField, type RuleSystemManifest, type RuleValues,
} from './ruleSchema'
import type { DataRow } from './ruleData'

export type SectionKind = 'overview' | 'rules' | 'data' | 'notes' | 'plugins'

export interface RuleSection {
  id: string
  title: string
  kind: SectionKind
  /** 侧边导航里的分组标题 */
  group: string
  /** rules 分区：属于它的组件（按预设顺序） */
  components: RuleComponent[]
}

/** 规则页的全部分区：概览 → 各规则分区（按组件 group，首次出现的顺序）→ 数据表 → 桌面约定 → 插件。 */
export function buildSections(manifest: RuleSystemManifest): RuleSection[] {
  const rules: RuleSection[] = []
  for (const c of manifest.components) {
    if (!c.group || !c.config_schema) continue
    let s = rules.find((x) => x.title === c.group)
    if (!s) {
      s = { id: `rules:${c.group}`, title: c.group, kind: 'rules', group: '规则', components: [] }
      rules.push(s)
    }
    s.components.push(c)
  }
  return [
    { id: 'overview', title: '概览', kind: 'overview', group: '本桌', components: [] },
    ...rules,
    { id: 'data', title: '数据表', kind: 'data', group: '数据', components: [] },
    { id: 'notes', title: '桌面约定', kind: 'notes', group: '叙事', components: [] },
    { id: 'plugins', title: '插件', kind: 'plugins', group: '扩展', components: [] },
  ]
}

export interface OptionMeta {
  key: string
  field: RuleField
  component: RuleComponent
  section: string
  /** 这个参数是不是组件的开关 */
  isToggle: boolean
  raw: unknown
}

/** 参数名 → 所属组件、字段、分区、原文值。 */
export function optionIndex(manifest: RuleSystemManifest): Map<string, OptionMeta> {
  const out = new Map<string, OptionMeta>()
  for (const s of buildSections(manifest)) {
    for (const c of s.components) {
      for (const f of componentFields(c)) {
        out.set(f.key, {
          key: f.key, field: f, component: c, section: s.id,
          isToggle: c.toggle === f.key, raw: f.default,
        })
      }
    }
  }
  return out
}

/** 全部参数的原文值。 */
export function rawValues(manifest: RuleSystemManifest): RuleValues {
  const out: RuleValues = {}
  optionIndex(manifest).forEach((m, k) => { out[k] = m.raw })
  return out
}

export function formatValue(field: RuleField, v: unknown): string {
  if (field.kind === 'boolean') return v ? '开' : '关'
  if (field.kind === 'choice') return field.choices?.find((c) => c.value === v)?.label ?? String(v)
  return String(v ?? '')
}

export interface Change {
  section: string
  /** 唯一键：参数名 / 'notes' / 'enabled' / '表名:主键' */
  key: string
  text: string
}

/** 这个参数此刻在页面上是否「算数」：所属可选组件关着、或 show_if 不满足时不算。 */
export function isLive(meta: OptionMeta, values: RuleValues): boolean {
  if (meta.isToggle) return true
  const t = meta.component.toggle
  if (t && !values[t]) return false
  return isShown(meta.field, values)
}

/** 与原文不同的参数（概览、分区空心数字用）。开关只在与默认不同时算一处。 */
export function diffFromRaw(index: Map<string, OptionMeta>, values: RuleValues): OptionMeta[] {
  return [...index.values()].filter((m) => isLive(m, values) && values[m.key] !== m.raw)
}

/** 草稿相对已保存的参数改动，写成人话：「大失败阈值 照规则书→只认 100」「流血 开」。 */
export function optionChanges(
  index: Map<string, OptionMeta>, saved: RuleValues, draft: RuleValues,
): Change[] {
  const out: Change[] = []
  index.forEach((m, k) => {
    if (saved[k] === draft[k]) return
    out.push({
      section: m.section, key: k,
      text: m.isToggle
        ? `${m.component.title} ${draft[k] ? '开' : '关'}`
        : `${m.field.title} ${formatValue(m.field, saved[k])}→${formatValue(m.field, draft[k])}`,
    })
  })
  return out
}

// ── 数据表 ────────────────────────────────────────────────────────────────

export type RowStatus = 'official' | 'modified' | 'added'

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)

/** 官方原样的行：已存的行里非新增者（改过的取原样）+ 已存时被删掉的官方行。 */
export function officialRows(
  view: { rows: DataRow[]; status: Record<string, string>; deleted?: DataRow[]; originals?: Record<string, DataRow> },
  key: string,
): DataRow[] {
  const kept = view.rows
    .filter((r) => view.status[String(r[key])] !== 'added')
    .map((r) => view.originals?.[String(r[key])] ?? r)
  return [...kept, ...(view.deleted ?? [])]
}

/** 一行相对官方数据的状态。 */
export function rowStatus(official: DataRow[], row: DataRow, key: string): RowStatus {
  const o = official.find((r) => r[key] === row[key])
  if (!o) return 'added'
  return same(o, row) ? 'official' : 'modified'
}

/** 官方行里，草稿中已不存在的（被删掉的）。 */
export function deletedRows(official: DataRow[], rows: DataRow[], key: string): DataRow[] {
  const keys = new Set(rows.map((r) => r[key]))
  return official.filter((r) => !keys.has(r[key]))
}

/** 一张表与官方数据有几处不同（新增 + 改过 + 删掉）。 */
export function tableDiffCount(official: DataRow[], rows: DataRow[], key: string): number {
  return rows.filter((r) => rowStatus(official, r, key) !== 'official').length
    + deletedRows(official, rows, key).length
}

/** 草稿表相对已保存表的改动：「武器表 +雷霆之锤」「武器表 改弓箭」「武器表 删长鞭」。 */
export function tableChanges(
  table: string, title: string, saved: DataRow[], draft: DataRow[], key: string,
): Change[] {
  const out: Change[] = []
  const before = new Map(saved.map((r) => [r[key], r]))
  const after = new Map(draft.map((r) => [r[key], r]))
  for (const [k, r] of after) {
    const b = before.get(k)
    if (!b) out.push({ section: 'data', key: `${table}:${String(k)}`, text: `${title} +${String(k)}` })
    else if (!same(b, r)) out.push({ section: 'data', key: `${table}:${String(k)}`, text: `${title} 改${String(k)}` })
  }
  for (const k of before.keys()) {
    if (!after.has(k)) out.push({ section: 'data', key: `${table}:${String(k)}`, text: `${title} 删${String(k)}` })
  }
  return out
}
