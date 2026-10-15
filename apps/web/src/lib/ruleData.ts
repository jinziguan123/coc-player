// 数据表编辑器的 schema 解读：后端 manifest 里每张数据表带一份行的 JSON Schema，
// 编辑器照它生成列表列与表单，不在前端写死任何一张表的字段。
//
// 放在 lib：纯函数、可单测，也不让组件文件失去 Fast Refresh。
import type { components } from '@/api/generated'

export type RuleTableManifest = components['schemas']['RuleTableManifest']
export type RuleTableView = components['schemas']['RuleTableView']
export type DataRow = Record<string, unknown>
export type RowStatus = 'official' | 'modified' | 'added'

export interface DataChoice {
  value: string
  label: string
  hint: string
}

/** 行里一个字段的编辑形态。 */
export interface DataField {
  key: string
  kind: 'string' | 'integer' | 'boolean' | 'choice' | 'string_list' | 'object_list'
  title: string
  hint?: string
  min?: number
  max?: number
  required: boolean
  default?: unknown
  choices?: DataChoice[]
  /** object_list 的子字段（如专精表的「专精 / 起始值」） */
  itemFields?: DataField[]
}

interface PropSchema {
  type?: string
  title?: string
  description?: string
  minimum?: number
  maximum?: number
  default?: unknown
  enum?: unknown[]
  items?: { type?: string; $ref?: string }
  'x-choices'?: DataChoice[]
}

interface ObjectSchema {
  properties?: Record<string, PropSchema>
  required?: string[]
  $defs?: Record<string, ObjectSchema>
}

function fieldsOf(schema: ObjectSchema, defs: Record<string, ObjectSchema>): DataField[] {
  const required = new Set(schema.required ?? [])
  return Object.entries(schema.properties ?? {}).map(([key, p]) => {
    const base = {
      key, title: p.title ?? key, hint: p.description, required: required.has(key), default: p.default,
    }
    if (p['x-choices']) return { ...base, kind: 'choice' as const, choices: p['x-choices'] }
    if (p.type === 'boolean') return { ...base, kind: 'boolean' as const }
    if (p.type === 'integer') return { ...base, kind: 'integer' as const, min: p.minimum, max: p.maximum }
    if (p.type === 'array') {
      const ref = p.items?.$ref?.split('/').pop()
      if (ref && defs[ref]) {
        return { ...base, kind: 'object_list' as const, itemFields: fieldsOf(defs[ref], defs) }
      }
      return { ...base, kind: 'string_list' as const }
    }
    return { ...base, kind: 'string' as const }
  })
}

/** 表的行 schema → 有序字段表。 */
export function tableFields(table: RuleTableManifest): DataField[] {
  const schema = table.row_schema as ObjectSchema
  return fieldsOf(schema, schema.$defs ?? {})
}

/** 新增一行时的初始值：取 schema 默认值，没有默认值的按类型给空值。 */
export function blankRow(fields: DataField[]): DataRow {
  const row: DataRow = {}
  for (const f of fields) {
    if (f.default !== undefined) row[f.key] = structuredClone(f.default)
    else if (f.kind === 'integer') row[f.key] = f.min ?? 0
    else if (f.kind === 'boolean') row[f.key] = false
    else if (f.kind === 'string_list' || f.kind === 'object_list') row[f.key] = []
    else row[f.key] = ''
  }
  return row
}

/** 列表里显示的几列：主键在前，再取最多三个简单字段（长文本、列表不进列）。 */
export function listColumns(fields: DataField[], key: string): DataField[] {
  const keyField = fields.find((f) => f.key === key)
  const rest = fields.filter((f) => f.key !== key && (f.kind === 'string' || f.kind === 'integer' || f.kind === 'choice'))
  return [...(keyField ? [keyField] : []), ...rest.slice(0, 3)]
}

/** 单元格的展示文本。 */
export function cellText(field: DataField, value: unknown): string {
  if (field.kind === 'choice') return field.choices?.find((c) => c.value === value)?.label ?? String(value ?? '')
  if (Array.isArray(value)) return value.map((v) => (typeof v === 'object' ? JSON.stringify(v) : String(v))).join('、')
  return String(value ?? '')
}

/** 本地编辑后的行状态：服务端原状态为基础，本地改过的官方行记「已修改」。 */
export function localStatus(
  key: unknown, serverStatus: Record<string, string>, editedKeys: Set<unknown>,
): RowStatus {
  const s = serverStatus[String(key)] as RowStatus | undefined
  if (!s) return 'added'
  if (s === 'official' && editedKeys.has(key)) return 'modified'
  return s
}

/** 搜索：任一字段的文本含关键字即命中。 */
export function matchesQuery(row: DataRow, query: string): boolean {
  const q = query.trim().toLowerCase()
  if (!q) return true
  return Object.values(row).some((v) => JSON.stringify(v ?? '').toLowerCase().includes(q))
}
