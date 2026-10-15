// 规则页的状态：已保存 vs 草稿。参数、总开关、桌面约定、数据表共用一份草稿，一次保存。
//
// 数据表按需读取（点开哪张读哪张）；没读过的表不会出现在保存请求里，后端也就不动它。
// 导入房规只是把内容载入草稿——看过、改过再保存，与平时改规则走同一条路。
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '@/api/client'
import type { components } from '@/api/generated'
import { invalidateRuleDataCache } from '@/components/character/useCocData'
import type { DataRow, RuleTableManifest, RuleTableView } from '@/lib/ruleData'
import {
  buildSections, diffFromRaw, officialRows, optionChanges, optionIndex, rawValues, tableChanges, tableDiffCount,
  type Change,
} from '@/lib/ruleDraft'
import type { RuleSystemManifest, RuleValues } from '@/lib/ruleSchema'

type VillageRulesRead = components['schemas']['VillageRulesRead']
type HouseRulesPreview = components['schemas']['HouseRulesPreviewRead']

interface Saved {
  options: RuleValues
  notes: string
  enabled: boolean
  /** 存着的数据表补丁（表名 → 补丁行）：没读过的表也能知道它改了几处 */
  data: Record<string, unknown[]>
  labels: string[]
}

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v)) as T
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)

function savedFrom(r: VillageRulesRead): Saved {
  return {
    options: r.effective, notes: r.table_notes ?? '', enabled: r.enabled !== false,
    data: (r.data ?? {}) as Record<string, unknown[]>, labels: r.labels ?? [],
  }
}

export function useRulesWorkbench(ruleSystem: string) {
  const [manifest, setManifest] = useState<RuleSystemManifest | null>(null)
  const [saved, setSaved] = useState<Saved | null>(null)
  const [options, setOptions] = useState<RuleValues>({})
  const [notes, setNotes] = useState('')
  const [enabled, setEnabled] = useState(true)
  /** 读过的表：服务端的样子（已保存） */
  const [views, setViews] = useState<Record<string, RuleTableView>>({})
  /** 读过的表：草稿行 */
  const [tableRows, setTableRows] = useState<Record<string, DataRow[]>>({})
  const [saving, setSaving] = useState(false)
  const [loadError, setLoadError] = useState('')

  const base = `/rulebooks/village-rules/${ruleSystem}`

  useEffect(() => {
    let alive = true
    Promise.all([api.get<VillageRulesRead>(base), api.get<RuleSystemManifest>(`${base}/manifest`)])
      .then(([r, m]) => {
        if (!alive) return
        const s = savedFrom(r)
        setManifest(m)
        setSaved(s)
        setOptions(clone(s.options))
        setNotes(s.notes)
        setEnabled(s.enabled)
        setViews({})
        setTableRows({})
      })
      .catch((e: unknown) => { if (alive) setLoadError(e instanceof Error ? e.message : '读取规则失败') })
    return () => { alive = false }
  }, [base])

  const loadTable = useCallback(async (name: string, rows?: DataRow[]) => {
    const v = views[name] ?? await api.get<RuleTableView>(`${base}/data/${name}`)
    setViews((cur) => ({ ...cur, [name]: v }))
    setTableRows((cur) => ({ ...cur, [name]: rows ?? cur[name] ?? clone(v.rows) }))
  }, [base, views])

  const index = useMemo(() => (manifest ? optionIndex(manifest) : new Map()), [manifest])
  const sections = useMemo(() => (manifest ? buildSections(manifest) : []), [manifest])
  const tables = useMemo<RuleTableManifest[]>(() => (manifest?.tables ?? []) as RuleTableManifest[], [manifest])

  /** 草稿相对已保存的全部改动（草稿条与分区实心数字）。 */
  const pending = useMemo<Change[]>(() => {
    if (!saved) return []
    const out = optionChanges(index, saved.options, options)
    if (enabled !== saved.enabled) out.unshift({ section: 'overview', key: 'enabled', text: `本桌规则 ${enabled ? '启用' : '停用'}` })
    for (const t of tables) {
      const v = views[t.name]
      const rows = tableRows[t.name]
      if (v && rows) out.push(...tableChanges(t.name, t.title, v.rows, rows, t.key))
    }
    if (notes.trim() !== saved.notes.trim()) out.push({ section: 'notes', key: 'notes', text: '桌面约定' })
    return out
  }, [saved, index, options, enabled, tables, views, tableRows, notes])

  const dirty = pending.length > 0

  const setOption = (key: string, value: unknown) => setOptions((cur) => ({ ...cur, [key]: value }))
  const setTable = (name: string, rows: DataRow[]) => setTableRows((cur) => ({ ...cur, [name]: rows }))

  const discard = () => {
    if (!saved) return
    setOptions(clone(saved.options))
    setNotes(saved.notes)
    setEnabled(saved.enabled)
    setTableRows(Object.fromEntries(Object.entries(views).map(([k, v]) => [k, clone(v.rows)])))
  }

  /** 一次提交全部改动；后端全部校验通过才落库，任何一处有错就一处都不改（抛出逐条原因）。 */
  const save = async () => {
    if (!saved) return
    setSaving(true)
    try {
      const dirtyTables = Object.fromEntries(
        Object.entries(tableRows).filter(([k, rows]) => views[k] && !same(rows, views[k].rows)),
      )
      const r = await api.put<VillageRulesRead>(base, {
        options, table_notes: notes, enabled,
        ...(Object.keys(dirtyTables).length ? { tables: dirtyTables } : {}),
      })
      const s = savedFrom(r)
      setSaved(s)
      setOptions(clone(s.options))
      setNotes(s.notes)
      setEnabled(s.enabled)
      // 存过的表重新读一次：状态（已改/新增/已删）以服务端为准
      const fresh = await Promise.all(Object.keys(dirtyTables).map(
        async (k) => [k, await api.get<RuleTableView>(`${base}/data/${k}`)] as const,
      ))
      if (fresh.length) {
        setViews((cur) => ({ ...cur, ...Object.fromEntries(fresh) }))
        setTableRows((cur) => ({ ...cur, ...Object.fromEntries(fresh.map(([k, v]) => [k, clone(v.rows)])) }))
      }
      invalidateRuleDataCache()
    } finally {
      setSaving(false)
    }
  }

  /** 把导入预览载入草稿：导入是整份替换，没写进文件的参数回到原文。 */
  const applyImport = async (preview: HouseRulesPreview) => {
    if (!manifest) return
    setOptions({ ...rawValues(manifest), ...(preview.options ?? {}) })
    setNotes(preview.table_notes ?? '')
    const full = (preview.tables ?? {}) as Record<string, DataRow[]>
    const loaded = await Promise.all(tables.map(
      async (t) => [t.name, views[t.name] ?? await api.get<RuleTableView>(`${base}/data/${t.name}`)] as const,
    ))
    setViews((cur) => ({ ...cur, ...Object.fromEntries(loaded) }))
    setTableRows((cur) => ({
      ...cur,
      ...Object.fromEntries(loaded.map(([k, v]) => [k, clone(full[k] ?? officialRows(v, tables.find((t) => t.name === k)!.key))])),
    }))
  }

  return {
    ruleSystem, manifest, saved, sections, tables, index, loadError,
    options, setOption, notes, setNotes, enabled, setEnabled,
    views, tableRows, loadTable, setTable,
    pending, dirty, saving, save, discard, applyImport,
  }
}

export type RulesWorkbench = ReturnType<typeof useRulesWorkbench>

/** 每个分区「与原文不同」的处数（已保存 + 草稿都算在内，即页面此刻的样子）。 */
export function diffCounts(wb: RulesWorkbench): Record<string, number> {
  const out: Record<string, number> = {}
  for (const meta of diffFromRaw(wb.index, wb.options)) out[meta.section] = (out[meta.section] ?? 0) + 1
  let data = 0
  for (const t of wb.tables) {
    const v = wb.views[t.name]
    const rows = wb.tableRows[t.name]
    data += v && rows ? tableDiffCount(officialRows(v, t.key), rows, t.key) : (wb.saved?.data[t.name]?.length ?? 0)
  }
  out.data = data
  out.notes = wb.notes.trim() ? 1 : 0
  return out
}
