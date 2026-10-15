// 规则页的参数行、可选规则卡与一个规则分区。
//
// 参数行：改过（与规则书原文不同）就在左侧亮一条黄铜线，并给出划掉的原文值和「恢复原文」。
// 可选规则卡：开关在卡头，开了卡片亮起、它的参数展开；来源标签（官方可选 / 本桌自定 /
// 官方变体 / 插件）让房主一眼分清哪些不是规则书里的。
import { RotateCcw } from 'lucide-react'
import { Collapse } from '@/components/ui/Collapse'
import { NumberField } from '@/components/ui/number-field'
import { Switch } from '@/components/ui/switch'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'
import { formatValue, isLive, type OptionMeta, type RuleSection } from '@/lib/ruleDraft'
import { componentFields, fieldHint, type RuleComponent, type RuleField, type RuleValues } from '@/lib/ruleSchema'

export interface SectionFilter {
  query: string
  onlyChanged: boolean
}

const matches = (q: string, ...texts: Array<string | undefined>) =>
  !q || texts.some((t) => (t ?? '').toLowerCase().includes(q.toLowerCase()))

function Control({ field, value, onChange }: { field: RuleField; value: unknown; onChange: (v: unknown) => void }) {
  if (field.kind === 'boolean') {
    return <Switch label={field.title} checked={Boolean(value)} onChange={onChange} />
  }
  if (field.kind === 'choice') {
    const choices = field.choices ?? []
    // 三项以内用分段按钮：选项一眼全看见，点一下就切；多了才收进下拉
    if (choices.length <= 3) {
      return (
        <div className="rules-seg" role="radiogroup" aria-label={field.title}>
          {choices.map((c) => (
            <button key={c.value} type="button" role="radio" aria-checked={c.value === value}
              className={c.value === value ? 'on' : ''} onClick={() => onChange(c.value)}>
              {c.label}
            </button>
          ))}
        </div>
      )
    }
    return (
      <Select value={String(value)} onValueChange={onChange}>
        <SelectTrigger className="w-40 text-sm" aria-label={field.title}><SelectValue /></SelectTrigger>
        <SelectContent>
          {choices.map((c) => <SelectItem key={c.value} value={c.value}>{c.label}</SelectItem>)}
        </SelectContent>
      </Select>
    )
  }
  return (
    <NumberField aria-label={field.title} value={typeof value === 'number' ? value : undefined}
      min={field.min} max={field.max} fallback={field.min ?? 0}
      onChange={(v) => { if (v !== undefined) onChange(Math.max(field.min ?? v, Math.min(field.max ?? v, v))) }}
      className="input w-20 text-right rules-num" />
  )
}

export function ParamRow({ meta, values, onChange, nested = false }: {
  meta: OptionMeta
  values: RuleValues
  onChange: (key: string, value: unknown) => void
  nested?: boolean
}) {
  const { field } = meta
  const value = values[field.key]
  const changed = value !== meta.raw
  return (
    <div id={`rule-${field.key}`} className={`rules-row ${changed ? 'changed' : ''} ${nested ? 'nested' : ''}`}>
      <div className="rules-row-label">{field.title}</div>
      <div className="rules-row-ctl">
        {changed && (
          <span className="rules-was">原文 <s>{formatValue(field, meta.raw)}</s></span>
        )}
        <Control field={field} value={value} onChange={(v) => onChange(field.key, v)} />
        <button type="button" className="rules-reset" aria-hidden={!changed} tabIndex={changed ? 0 : -1}
          title="恢复规则书原文" onClick={() => onChange(field.key, meta.raw)}>
          <RotateCcw size={12} aria-hidden /> 恢复原文
        </button>
      </div>
      <div className="rules-row-hint">{fieldHint(field, value)}</div>
    </div>
  )
}

function tagOf(c: RuleComponent): { text: string; cls: string } {
  if ((c.source ?? 'official').startsWith('plugin:')) return { text: '插件', cls: 'plugin' }
  const tag = c.tag || '可选'
  return { text: tag, cls: tag.includes('自定') ? 'house' : 'official' }
}

export function RuleCard({ component, metas, values, onChange, filter }: {
  component: RuleComponent
  metas: OptionMeta[]
  values: RuleValues
  onChange: (key: string, value: unknown) => void
  filter: SectionFilter
}) {
  const toggle = metas.find((m) => m.isToggle)!
  const on = Boolean(values[toggle.key])
  const inner = metas.filter((m) => !m.isToggle && isLive(m, values))
  const anyChanged = on !== toggle.raw || inner.some((m) => values[m.key] !== m.raw)
  if (filter.onlyChanged && !anyChanged) return null
  if (!matches(filter.query, component.title, component.description, ...inner.map((m) => m.field.title))) return null
  const tag = tagOf(component)
  return (
    <div id={`rule-${toggle.key}`} className={`rules-card ${on ? 'on' : ''}`}>
      <div className="rules-card-head" onClick={() => onChange(toggle.key, !on)}>
        <div className="min-w-0 flex-1">
          <div className="rules-card-title">
            {component.title}<span className={`rules-tag ${tag.cls}`}>{tag.text}</span>
            {on !== toggle.raw && <span className="rules-was">原文 <s>{toggle.raw ? '开' : '关'}</s></span>}
          </div>
          <div className="rules-card-desc">{component.description}</div>
        </div>
        {/* 点卡头任何位置都切换；开关本身阻止冒泡，免得切两次 */}
        <span onClick={(e) => e.stopPropagation()}>
          <Switch label={component.title} checked={on} onChange={(v) => onChange(toggle.key, v)} />
        </span>
      </div>
      <Collapse open={on && inner.length > 0}>
        <div className="rules-card-body">
          {inner.map((m) => <ParamRow key={m.key} meta={m} values={values} onChange={onChange} nested />)}
        </div>
      </Collapse>
    </div>
  )
}

/** 一个规则分区：先列常驻组件的参数，再列可选规则卡。 */
export function RulesSectionView({ section, index, values, onChange, filter, hint }: {
  section: RuleSection
  index: Map<string, OptionMeta>
  values: RuleValues
  onChange: (key: string, value: unknown) => void
  filter: SectionFilter
  hint?: string
}) {
  const metasOf = (c: RuleComponent) => componentFields(c).map((f) => index.get(f.key)!).filter(Boolean)
  const fixed = section.components.filter((c) => !c.toggle)
  const optional = section.components.filter((c) => c.toggle)
  const rows = fixed.flatMap(metasOf)
    .filter((m) => isLive(m, values))
    .filter((m) => !filter.onlyChanged || values[m.key] !== m.raw)
    .filter((m) => matches(filter.query, m.field.title, m.field.hint))
  const cards = optional.map((c) => (
    <RuleCard key={c.id} component={c} metas={metasOf(c)} values={values} onChange={onChange} filter={filter} />
  ))
  const anyCard = optional.some((c) => {
    const metas = metasOf(c)
    const t = metas.find((m) => m.isToggle)
    if (!t) return false
    const inner = metas.filter((m) => !m.isToggle && isLive(m, values))
    const changed = values[t.key] !== t.raw || inner.some((m) => values[m.key] !== m.raw)
    return (!filter.onlyChanged || changed)
      && matches(filter.query, c.title, c.description, ...inner.map((m) => m.field.title))
  })
  if (!rows.length && !anyCard) return null
  return (
    <section id={`sec-${section.id}`} className="rules-sec" aria-label={section.title}>
      <div className="rules-sec-head"><h3>{section.title}</h3>{hint && <span>{hint}</span>}</div>
      <div className="rules-sec-rule" />
      {rows.map((m) => <ParamRow key={m.key} meta={m} values={values} onChange={onChange} />)}
      {cards}
    </section>
  )
}
