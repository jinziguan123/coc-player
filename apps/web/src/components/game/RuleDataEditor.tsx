// 数据表编辑器：改武器、职业、技能、专精、装备、疯狂症状这些表。
//
// 一份组件服务两层：规则书页的「村规」（这一桌所有房间共用）与模组页的「模组自带」。
// 字段、列、校验口径全来自后端 manifest 里的行 schema——前端不写死任何一张表。
//
// 保存提交的是**整张表**，服务端只存与官方数据的差异。官方行可以改、可以删（删了能恢复），
// 改过的行能一键恢复官方原样；新增的行就是新增。
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Plus, RotateCcw, Save, Search, Trash2, Undo2 } from 'lucide-react'
import { toast } from 'sonner'
import { api } from '@/api/client'
import { invalidateRuleDataCache } from '@/components/character/useCocData'
import { NumberField } from '@/components/ui/number-field'
import { Switch } from '@/components/ui/switch'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'
import {
  blankRow, cellText, listColumns, localStatus, matchesQuery, tableFields,
  type DataField, type DataRow, type RowStatus, type RuleTableManifest, type RuleTableView,
} from '@/lib/ruleData'

export type RuleDataScope =
  | { kind: 'village'; ruleSystem: string }
  | { kind: 'module'; ruleSystem: string; moduleId: string }

function tableUrl(scope: RuleDataScope, table: string): string {
  return scope.kind === 'village'
    ? `/rulebooks/village-rules/${scope.ruleSystem}/data/${table}`
    : `/modules/${scope.moduleId}/rule-data/${table}`
}

const STATUS_LABEL: Record<RowStatus, string> = { official: '官方', modified: '已修改', added: '新增' }

function StatusChip({ status }: { status: RowStatus }) {
  if (status === 'official') return null
  return (
    <span className="chip" style={{
      fontSize: 'var(--text-2xs)',
      color: status === 'added' ? 'var(--color-success, var(--color-accent))' : 'var(--color-accent)',
    }}>
      {STATUS_LABEL[status]}
    </span>
  )
}

/** 一个字段的输入控件。 */
function FieldInput({ field, value, onChange, disabled, label }: {
  field: DataField
  value: unknown
  onChange: (v: unknown) => void
  disabled?: boolean
  label: string
}) {
  switch (field.kind) {
    case 'boolean':
      return <Switch label={label} checked={Boolean(value)} onChange={onChange} disabled={disabled} />
    case 'integer':
      return (
        <NumberField aria-label={label} value={typeof value === 'number' ? value : undefined}
          min={field.min} max={field.max} fallback={field.min ?? 0} disabled={disabled}
          onChange={(v) => onChange(v ?? field.min ?? 0)} className="input w-24 text-sm text-right" />
      )
    case 'choice':
      return (
        <Select value={String(value ?? '')} onValueChange={onChange} disabled={disabled}>
          <SelectTrigger className="w-40 text-sm" aria-label={label}><SelectValue /></SelectTrigger>
          <SelectContent>
            {field.choices?.map((c) => (
              // 空字符串不能作 Radix Select 的值，用占位映射
              <SelectItem key={c.value || '__none'} value={c.value || '__none'}>{c.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      )
    case 'string_list':
      return (
        <textarea aria-label={label} rows={3} disabled={disabled}
          value={Array.isArray(value) ? value.join('\n') : ''}
          onChange={(e) => onChange(e.target.value.split('\n').map((s) => s.trim()).filter(Boolean))}
          placeholder="每行一项" className="input w-full text-sm resize-y" />
      )
    case 'object_list': {
      const items = Array.isArray(value) ? (value as DataRow[]) : []
      const sub = field.itemFields ?? []
      const setItem = (i: number, next: DataRow) => onChange(items.map((it, j) => (j === i ? next : it)))
      return (
        <div className="space-y-1.5 w-full">
          {items.map((it, i) => (
            <div key={i} className="flex items-center gap-2">
              {sub.map((sf) => (
                <FieldInput key={sf.key} field={sf} value={it[sf.key]} label={`${label}·${sf.title}`}
                  disabled={disabled} onChange={(v) => setItem(i, { ...it, [sf.key]: v })} />
              ))}
              <button type="button" onClick={() => onChange(items.filter((_, j) => j !== i))}
                disabled={disabled} className="btn-secondary btn-sm" aria-label={`删除${label}第 ${i + 1} 项`}>
                <Trash2 size={12} aria-hidden />
              </button>
            </div>
          ))}
          <button type="button" onClick={() => onChange([...items, blankRow(sub)])} disabled={disabled}
            className="btn-secondary btn-sm inline-flex items-center gap-1">
            <Plus size={12} aria-hidden /> 加一项
          </button>
        </div>
      )
    }
    default:
      return (
        <input aria-label={label} value={String(value ?? '')} disabled={disabled}
          onChange={(e) => onChange(e.target.value)} className="input w-full text-sm" />
      )
  }
}

/** 选中行的表单。主键在已有行上不可改——改名等于删一行再加一行，免得悄悄把官方行改没了。 */
function RowForm({ fields, row, keyField, keyLocked, onChange }: {
  fields: DataField[]
  row: DataRow
  keyField: string
  keyLocked: boolean
  onChange: (next: DataRow) => void
}) {
  return (
    <div className="space-y-3">
      {fields.map((f) => {
        const wide = f.kind === 'string' || f.kind === 'string_list' || f.kind === 'object_list'
        const locked = f.key === keyField && keyLocked
        const value = f.kind === 'choice' && row[f.key] === '' ? '__none' : row[f.key]
        return (
          <div key={f.key} className={wide ? '' : 'flex items-center justify-between gap-3'}>
            <div className={wide ? 'mb-1' : 'min-w-0'}>
              <div className="text-sm">{f.title}{f.required && <span aria-hidden> *</span>}</div>
              {(f.hint || locked) && (
                <div style={{ fontSize: 'var(--text-2xs)', color: 'var(--color-text-secondary)' }}>
                  {locked ? '已有行的主键不能改；要改名请删掉这一行再新增。' : f.hint}
                </div>
              )}
            </div>
            <FieldInput field={f} value={value} label={f.title} disabled={locked}
              onChange={(v) => onChange({ ...row, [f.key]: v === '__none' ? '' : v })} />
          </div>
        )
      })}
    </div>
  )
}

interface Draft {
  rows: DataRow[]
  deleted: DataRow[]
  /** 本地改过的官方行的主键 */
  edited: Set<unknown>
}

function draftFrom(view: RuleTableView): Draft {
  return { rows: view.rows.map((r) => ({ ...r })), deleted: [...(view.deleted ?? [])], edited: new Set() }
}

export function RuleDataEditor({ scope, onSaved }: {
  scope: RuleDataScope
  /** 保存成功后回调（如让规矩摘要刷新） */
  onSaved?: () => void
}) {
  const [tables, setTables] = useState<RuleTableManifest[] | null>(null)
  const [active, setActive] = useState<string>('')
  const [view, setView] = useState<RuleTableView | null>(null)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [selected, setSelected] = useState<number | null>(null)
  const [query, setQuery] = useState('')
  const [saving, setSaving] = useState(false)
  const [errors, setErrors] = useState<string[]>([])
  const [dirty, setDirty] = useState(false)

  useEffect(() => {
    let alive = true
    api.get<{ tables?: RuleTableManifest[] }>(`/rulebooks/village-rules/${scope.ruleSystem}/manifest`)
      .then((m) => {
        if (!alive) return
        setTables(m.tables ?? [])
        setActive((cur) => cur || m.tables?.[0]?.name || '')
      })
      .catch((e: unknown) => toast.error(e instanceof Error ? e.message : '读取数据表失败'))
    return () => { alive = false }
  }, [scope.ruleSystem])

  const load = useCallback(async (table: string) => {
    if (!table) return
    try {
      const v = await api.get<RuleTableView>(tableUrl(scope, table))
      setView(v)
      setDraft(draftFrom(v))
      setSelected(null)
      setDirty(false)
      setErrors([])
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : '读取数据表失败')
    }
  // scope 是对象字面量，按它的内容而不是引用决定是否重取
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope.kind, scope.ruleSystem, scope.kind === 'module' ? scope.moduleId : ''])

  useEffect(() => { void load(active) }, [active, load])

  const table = tables?.find((t) => t.name === active)
  const fields = useMemo(() => (table ? tableFields(table) : []), [table])
  const columns = useMemo(() => (table ? listColumns(fields, table.key) : []), [fields, table])

  if (!tables || !table || !view || !draft) {
    return <div className="text-sm py-6 text-center" style={{ color: 'var(--color-text-secondary)' }}>读取中…</div>
  }

  const key = table.key
  const statusOf = (row: DataRow): RowStatus => localStatus(row[key], view.status, draft.edited)
  const visible = draft.rows.map((r, i) => ({ r, i })).filter(({ r }) => matchesQuery(r, query))
  const current = selected != null ? draft.rows[selected] : null

  const update = (next: Draft) => { setDraft(next); setDirty(true) }

  const editRow = (i: number, row: DataRow) => {
    const edited = new Set(draft.edited)
    if (view.status[String(row[key])]) edited.add(row[key])
    update({ ...draft, rows: draft.rows.map((r, j) => (j === i ? row : r)), edited })
  }

  const addRow = () => {
    update({ ...draft, rows: [...draft.rows, blankRow(fields)] })
    setSelected(draft.rows.length)
    setQuery('')
  }

  const deleteRow = (i: number) => {
    const row = draft.rows[i]
    const k = row[key]
    const isServerRow = Boolean(view.status[String(k)])
    // 官方行（含改过的）删掉后进「已删除」区，可以恢复成官方原样
    const original = view.originals?.[String(k)] ?? (view.status[String(k)] === 'official' ? row : null)
    update({
      ...draft,
      rows: draft.rows.filter((_, j) => j !== i),
      deleted: isServerRow && original && view.status[String(k)] !== 'added'
        ? [...draft.deleted, original] : draft.deleted,
    })
    setSelected(null)
  }

  const restoreDeleted = (row: DataRow) => {
    const edited = new Set(draft.edited)
    edited.delete(row[key])
    update({
      rows: [...draft.rows, { ...row }],
      deleted: draft.deleted.filter((d) => d[key] !== row[key]),
      edited,
    })
  }

  const restoreOfficial = (i: number) => {
    const k = draft.rows[i][key]
    const original = view.originals?.[String(k)]
    if (!original) return
    const edited = new Set(draft.edited)
    edited.delete(k)
    // 服务端记为「已修改」的行恢复后，保存时差异消失，状态随之回到官方
    update({ ...draft, rows: draft.rows.map((r, j) => (j === i ? { ...original } : r)), edited })
  }

  const save = async () => {
    const missing = draft.rows.findIndex((r) => !String(r[key] ?? '').trim())
    if (missing >= 0) {
      setErrors([`第 ${missing + 1} 行还没填${fields.find((f) => f.key === key)?.title ?? key}`])
      setSelected(missing)
      return
    }
    setSaving(true)
    try {
      const v = await api.put<RuleTableView>(tableUrl(scope, active), { rows: draft.rows })
      setView(v)
      setDraft(draftFrom(v))
      setDirty(false)
      setErrors([])
      setSelected(null)
      invalidateRuleDataCache()   // 建卡页的武器/专精缓存作废，下次打开取新表
      toast.success(`${table.title}已保存，下一次结算起生效`)
      onSaved?.()
    } catch (e: unknown) {
      // 服务端把逐行原因用换行拼在一起
      setErrors((e instanceof Error ? e.message : '保存失败').split('\n').filter(Boolean))
    } finally {
      setSaving(false)
    }
  }

  const modifiedCount = draft.rows.filter((r) => statusOf(r) !== 'official').length + draft.deleted.length

  return (
    <div>
      <div className="flex flex-wrap gap-1.5 mb-2" role="tablist" aria-label="数据表">
        {tables.map((t) => (
          <button key={t.name} role="tab" aria-selected={t.name === active}
            disabled={dirty && t.name !== active}
            title={dirty && t.name !== active ? '先保存或放弃当前表的改动' : t.description}
            onClick={() => setActive(t.name)}
            className={t.name === active ? 'btn-primary btn-sm' : 'btn-secondary btn-sm'}>
            {t.title}
          </button>
        ))}
      </div>
      <div className="text-xs mb-3" style={{ color: 'var(--color-text-secondary)' }}>
        {table.description}。{scope.kind === 'village'
          ? '这里的改动对这一桌所有房间生效（含进行中的局）。'
          : '这里的改动随模组走，房主的村规可以再覆盖它。'}
        {modifiedCount > 0 && ` 当前与官方数据有 ${modifiedCount} 处不同。`}
      </div>

      <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
        {/* 左：行列表 */}
        <div className="min-w-0">
          <div className="flex gap-2 mb-2">
            <label className="relative flex-1">
              <Search size={13} aria-hidden className="absolute left-2 top-1/2 -translate-y-1/2"
                style={{ color: 'var(--color-text-secondary)' }} />
              <input value={query} onChange={(e) => setQuery(e.target.value)} aria-label="搜索行"
                placeholder={`在${table.title}里搜索`} className="input w-full text-sm pl-7" />
            </label>
            <button onClick={addRow} className="btn-secondary btn-sm inline-flex items-center gap-1">
              <Plus size={13} aria-hidden /> 新增
            </button>
          </div>
          <div className="rounded-md overflow-auto" style={{ maxHeight: '26rem', border: '1px solid var(--color-border)' }}>
            <table className="w-full text-sm">
              <thead>
                <tr style={{ background: 'var(--color-bg-tertiary)' }}>
                  {columns.map((c) => <th key={c.key} className="text-left font-normal px-2 py-1.5 whitespace-nowrap">{c.title}</th>)}
                  <th className="w-16" aria-label="状态" />
                </tr>
              </thead>
              <tbody>
                {visible.map(({ r, i }) => (
                  <tr key={i} onClick={() => setSelected(i)} aria-selected={selected === i}
                    className="cursor-pointer"
                    style={{
                      borderTop: '1px solid var(--color-border)',
                      background: selected === i ? 'var(--color-bg-tertiary)' : undefined,
                    }}>
                    {columns.map((c) => (
                      <td key={c.key} className="px-2 py-1 truncate max-w-[10rem]">
                        {cellText(c, r[c.key]) || <span style={{ color: 'var(--color-text-secondary)' }}>（未填）</span>}
                      </td>
                    ))}
                    <td className="px-2 py-1 text-right"><StatusChip status={statusOf(r)} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
            {visible.length === 0 && (
              <div className="text-xs py-4 text-center" style={{ color: 'var(--color-text-secondary)' }}>没有匹配的行</div>
            )}
          </div>

          {draft.deleted.length > 0 && (
            <div className="mt-3">
              <div className="text-xs mb-1" style={{ color: 'var(--color-text-secondary)' }}>
                已删除的官方行（{draft.deleted.length}）
              </div>
              <div className="flex flex-wrap gap-1.5">
                {draft.deleted.map((d) => (
                  <button key={String(d[key])} onClick={() => restoreDeleted(d)}
                    className="chip inline-flex items-center gap-1" title="恢复这一行">
                    <Undo2 size={11} aria-hidden /> {String(d[key])}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* 右：选中行的表单 */}
        <div className="min-w-0">
          {current && selected != null ? (
            <div className="rounded-md p-3" style={{ border: '1px solid var(--color-border)' }}>
              <div className="flex items-center justify-between gap-2 mb-3">
                <div className="text-sm flex items-center gap-2" style={{ color: 'var(--color-text-primary)' }}>
                  {String(current[key] || '新的一行')} <StatusChip status={statusOf(current)} />
                </div>
                <div className="flex gap-1.5">
                  {statusOf(current) === 'modified' && view.originals?.[String(current[key])] && (
                    <button onClick={() => restoreOfficial(selected)}
                      className="btn-secondary btn-sm inline-flex items-center gap-1">
                      <RotateCcw size={12} aria-hidden /> 恢复官方
                    </button>
                  )}
                  <button onClick={() => deleteRow(selected)}
                    className="btn-secondary btn-sm inline-flex items-center gap-1">
                    <Trash2 size={12} aria-hidden /> 删除
                  </button>
                </div>
              </div>
              <RowForm fields={fields} row={current} keyField={key}
                keyLocked={statusOf(current) !== 'added'}
                onChange={(next) => editRow(selected, next)} />
            </div>
          ) : (
            <div className="text-sm rounded-md p-6 text-center" style={{
              border: '1px dashed var(--color-border)', color: 'var(--color-text-secondary)',
            }}>
              点左边的一行查看或修改，或者「新增」一行。
            </div>
          )}
        </div>
      </div>

      {errors.length > 0 && (
        <ul role="alert" className="mt-3 text-xs list-disc pl-4" style={{ color: 'var(--color-danger)' }}>
          {errors.map((e) => <li key={e}>{e}</li>)}
        </ul>
      )}

      <div className="flex justify-end gap-2 mt-3">
        {dirty && (
          <button onClick={() => { setDraft(draftFrom(view)); setDirty(false); setErrors([]); setSelected(null) }}
            className="btn-secondary btn-sm">
            放弃改动
          </button>
        )}
        <button onClick={() => void save()} disabled={saving || !dirty}
          className="btn-primary btn-sm inline-flex items-center gap-1">
          <Save size={13} aria-hidden /> {saving ? '保存中…' : `保存${table.title}`}
        </button>
      </div>
    </div>
  )
}
