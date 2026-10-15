// 数据表的列表 + 行表单（受控）：行由调用方持有，改动经 onChange 交回。
//
// 规则页把它接进全页的统一草稿；模组页的 RuleDataEditor 自己存一份、单独保存。
// 每行的状态（官方 / 已改 / 新增）与「已删除的官方行」都由「官方数据 vs 当前行」现算，
// 不在本地另记一份——两边对不上的那类 bug 就无从发生。
import { useMemo, useState } from 'react'
import { Plus, RotateCcw, Search, Trash2, Undo2 } from 'lucide-react'
import { NumberField } from '@/components/ui/number-field'
import { Switch } from '@/components/ui/switch'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'
import {
  blankRow, cellText, listColumns, matchesQuery, tableFields,
  type DataField, type DataRow, type RuleTableManifest,
} from '@/lib/ruleData'
import { deletedRows, rowStatus, type RowStatus } from '@/lib/ruleDraft'

const STATUS_LABEL: Record<RowStatus, string> = { official: '官方', modified: '已修改', added: '新增' }

export function StatusChip({ status }: { status: RowStatus }) {
  if (status === 'official') return null
  return (
    <span className="chip" style={{
      fontSize: 'var(--text-2xs)',
      color: status === 'added' ? 'var(--color-verdigris)' : 'var(--color-accent)',
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
        <Select value={String(value || '__none')} onValueChange={(v) => onChange(v === '__none' ? '' : v)} disabled={disabled}>
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
            <FieldInput field={f} value={row[f.key]} label={f.title} disabled={locked}
              onChange={(v) => onChange({ ...row, [f.key]: v })} />
          </div>
        )
      })}
    </div>
  )
}

export function DataTableView({ table, official, rows, onChange, query: externalQuery, onlyChanged = false }: {
  table: RuleTableManifest
  /** 官方原样的行：判定每行状态与「已删除」的基准 */
  official: DataRow[]
  /** 当前（草稿）行 */
  rows: DataRow[]
  onChange: (rows: DataRow[]) => void
  /** 外部给的搜索词（规则页顶部搜索框）；不给则显示自带的搜索框 */
  query?: string
  /** 只显示与官方不同的行 */
  onlyChanged?: boolean
}) {
  const key = table.key
  const fields = useMemo(() => tableFields(table), [table])
  const columns = useMemo(() => listColumns(fields, key), [fields, key])
  const [selected, setSelected] = useState<number | null>(null)
  const [ownQuery, setOwnQuery] = useState('')
  const query = externalQuery ?? ownQuery

  const deleted = deletedRows(official, rows, key)
  const statusOf = (row: DataRow) => rowStatus(official, row, key)
  const visible = rows.map((r, i) => ({ r, i }))
    .filter(({ r }) => matchesQuery(r, query))
    .filter(({ r }) => !onlyChanged || statusOf(r) !== 'official')
  const current = selected != null ? rows[selected] : null
  const original = current ? official.find((o) => o[key] === current[key]) : undefined

  const edit = (i: number, row: DataRow) => onChange(rows.map((r, j) => (j === i ? row : r)))
  const add = () => {
    onChange([...rows, blankRow(fields)])
    setSelected(rows.length)
    setOwnQuery('')
  }
  const remove = (i: number) => {
    onChange(rows.filter((_, j) => j !== i))
    setSelected(null)
  }

  return (
    <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
      <div className="min-w-0">
        <div className="flex gap-2 mb-2">
          {externalQuery === undefined && (
            <label className="relative flex-1">
              <Search size={13} aria-hidden className="absolute left-2 top-1/2 -translate-y-1/2"
                style={{ color: 'var(--color-text-secondary)' }} />
              <input value={ownQuery} onChange={(e) => setOwnQuery(e.target.value)} aria-label="搜索行"
                placeholder={`在${table.title}里搜索`} className="input w-full text-sm pl-7" />
            </label>
          )}
          <button onClick={add} className="btn-secondary btn-sm inline-flex items-center gap-1 ml-auto">
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
                <tr key={i} onClick={() => setSelected(i)} aria-selected={selected === i} className="cursor-pointer"
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
            <div className="text-xs py-4 text-center" style={{ color: 'var(--color-text-secondary)' }}>
              {onlyChanged ? '这张表和官方数据一样' : '没有匹配的行'}
            </div>
          )}
        </div>

        {deleted.length > 0 && (
          <div className="mt-3">
            <div className="text-xs mb-1" style={{ color: 'var(--color-text-secondary)' }}>
              已删除的官方行（{deleted.length}）
            </div>
            <div className="flex flex-wrap gap-1.5">
              {deleted.map((d) => (
                <button key={String(d[key])} onClick={() => onChange([...rows, { ...d }])}
                  className="chip inline-flex items-center gap-1" title="恢复这一行">
                  <Undo2 size={11} aria-hidden /> {String(d[key])}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      <div className="min-w-0">
        {current && selected != null ? (
          <div className="rounded-md p-3" style={{ border: '1px solid var(--color-border)' }}>
            <div className="flex items-center justify-between gap-2 mb-3">
              <div className="text-sm flex items-center gap-2" style={{ color: 'var(--color-text-primary)' }}>
                {String(current[key] || '新的一行')} <StatusChip status={statusOf(current)} />
              </div>
              <div className="flex gap-1.5">
                {statusOf(current) === 'modified' && original && (
                  <button onClick={() => edit(selected, { ...original })}
                    className="btn-secondary btn-sm inline-flex items-center gap-1">
                    <RotateCcw size={12} aria-hidden /> 恢复官方
                  </button>
                )}
                <button onClick={() => remove(selected)} className="btn-secondary btn-sm inline-flex items-center gap-1">
                  <Trash2 size={12} aria-hidden /> 删除
                </button>
              </div>
            </div>
            <RowForm fields={fields} row={current} keyField={key}
              keyLocked={statusOf(current) !== 'added'} onChange={(next) => edit(selected, next)} />
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
  )
}
