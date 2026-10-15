// 规则页的 schema 解读：后端 manifest 里每个组件带一份参数 JSON Schema，
// 面板照它渲染，不再在前端写死字段、标题、区间与说明。
//
// 放在 lib 而不是组件文件里：纯函数、可单测，也不让组件文件失去 Fast Refresh。
import type { components } from '@/api/generated'

export type RuleSystemManifest = components['schemas']['RuleSystemManifest']
export type RuleComponent = components['schemas']['RuleComponentManifest']
export type RuleValues = Record<string, unknown>

export interface RuleChoice {
  value: string
  label: string
  hint: string
}

/** 一个参数的界面描述（只取面板用得到的那几项）。 */
export interface RuleField {
  key: string
  kind: 'boolean' | 'integer' | 'choice'
  title: string
  hint?: string
  min?: number
  max?: number
  choices?: RuleChoice[]
  showIf?: RuleValues
  /** 规则书原文的取值（schema 默认值） */
  default?: unknown
}

interface PropertySchema {
  type?: string
  title?: string
  description?: string
  minimum?: number
  maximum?: number
  default?: unknown
  'x-choices'?: RuleChoice[]
  'x-show-if'?: RuleValues
}

/** 组件参数 schema → 有序字段表。 */
export function componentFields(component: RuleComponent): RuleField[] {
  const props = (component.config_schema?.properties ?? {}) as Record<string, PropertySchema>
  return Object.entries(props).map(([key, p]) => ({
    key,
    kind: p['x-choices'] ? 'choice' : p.type === 'boolean' ? 'boolean' : 'integer',
    title: p.title ?? key,
    hint: p.description,
    min: p.minimum,
    max: p.maximum,
    choices: p['x-choices'],
    showIf: p['x-show-if'],
    default: p.default,
  }))
}

/** 面板分栏：按组件出现的顺序收集 group，没有 group 的组件（无可调参数）不上面板。 */
export function panelGroups(manifest: RuleSystemManifest): { name: string; components: RuleComponent[] }[] {
  const groups: { name: string; components: RuleComponent[] }[] = []
  for (const c of manifest.components) {
    if (!c.group || !c.config_schema) continue
    let g = groups.find((x) => x.name === c.group)
    if (!g) {
      g = { name: c.group, components: [] }
      groups.push(g)
    }
    g.components.push(c)
  }
  return groups
}

/** ``x-show-if`` 是否满足：所列参数都取指定值时才显示。 */
export function isShown(field: RuleField, values: RuleValues): boolean {
  if (!field.showIf) return true
  return Object.entries(field.showIf).every(([k, v]) => values[k] === v)
}

/** 说明文字：枚举取当前选项的说明；其余代入 ``{value}``。 */
export function fieldHint(field: RuleField, value: unknown): string | undefined {
  if (field.kind === 'choice') {
    return field.choices?.find((c) => c.value === value)?.hint ?? field.hint
  }
  return field.hint?.replace('{value}', String(value))
}
