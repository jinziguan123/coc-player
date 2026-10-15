// 模组自带的数据表编辑：模组页「规则数据」视图用。叠在官方数据之上、村规之下。
//
// 列表与表单是共用的 DataTableView；这里只管读、存这一层（模组层有自己的保存按钮，
// 与模组正文的编辑草稿互不相干）。这一桌的村规数据表在「规则」页，那里与参数一起统一保存。
import { useCallback, useEffect, useState } from 'react'
import { Save } from 'lucide-react'
import { toast } from 'sonner'
import { api } from '@/api/client'
import { invalidateRuleDataCache } from '@/components/character/useCocData'
import { DataTableView } from '@/components/game/DataTableView'
import type { DataRow, RuleTableManifest, RuleTableView } from '@/lib/ruleData'
import { officialRows } from '@/lib/ruleDraft'

export interface RuleDataScope {
  ruleSystem: string
  moduleId: string
}

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)

export function RuleDataEditor({ scope }: { scope: RuleDataScope }) {
  const [tables, setTables] = useState<RuleTableManifest[] | null>(null)
  const [active, setActive] = useState('')
  const [view, setView] = useState<RuleTableView | null>(null)
  const [rows, setRows] = useState<DataRow[]>([])
  const [saving, setSaving] = useState(false)
  const [errors, setErrors] = useState<string[]>([])
  const url = (table: string) => `/modules/${scope.moduleId}/rule-data/${table}`

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
      const v = await api.get<RuleTableView>(`/modules/${scope.moduleId}/rule-data/${table}`)
      setView(v)
      setRows(v.rows.map((r) => ({ ...r })))
      setErrors([])
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : '读取数据表失败')
    }
  }, [scope.moduleId])

  useEffect(() => { void load(active) }, [active, load])

  const table = tables?.find((t) => t.name === active)
  if (!tables || !table || !view) {
    return <div className="text-sm py-6 text-center" style={{ color: 'var(--color-text-secondary)' }}>读取中…</div>
  }

  const dirty = !same(rows, view.rows)
  const save = async () => {
    const missing = rows.findIndex((r) => !String(r[table.key] ?? '').trim())
    if (missing >= 0) {
      setErrors([`第 ${missing + 1} 行还没填主键`])
      return
    }
    setSaving(true)
    try {
      const v = await api.put<RuleTableView>(url(active), { rows })
      setView(v)
      setRows(v.rows.map((r) => ({ ...r })))
      setErrors([])
      invalidateRuleDataCache()
      toast.success(`${table.title}已保存，下一次结算起生效`)
    } catch (e: unknown) {
      // 服务端把逐行原因用换行拼在一起
      setErrors((e instanceof Error ? e.message : '保存失败').split('\n').filter(Boolean))
    } finally {
      setSaving(false)
    }
  }

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
        {table.description}。这里的改动随模组走，这一桌的村规可以再覆盖它。
      </div>

      <DataTableView key={active} table={table} official={officialRows(view, table.key)} rows={rows} onChange={setRows} />

      {errors.length > 0 && (
        <ul role="alert" className="mt-3 text-xs list-disc pl-4" style={{ color: 'var(--color-danger)' }}>
          {errors.map((e) => <li key={e}>{e}</li>)}
        </ul>
      )}

      <div className="flex justify-end gap-2 mt-3">
        {dirty && (
          <button onClick={() => { setRows(view.rows.map((r) => ({ ...r }))); setErrors([]) }} className="btn-secondary btn-sm">
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
