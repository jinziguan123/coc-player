// 村规：这一桌长期沿用的规则改动，按规则系统配置（规则书页面）。
//
// 层级：模组默认 > 村规 > 本局覆盖 > 规则原文。留在默认档不是「没设」，是「照规则书来」。
// 后端只落与原文的差异项，所以日后规则默认值调整时，没显式改过的项会跟着走。
//
// 它不在房间里设——村规是桌上的规矩，不该每开一局重填一遍。改动对所有用这套规则的
// 房间即时生效（含进行中的局）。
import { useEffect, useRef, useState } from 'react'
import { Download, Upload } from 'lucide-react'
import { toast } from 'sonner'
import { api } from '@/api/client'
import type { components } from '@/api/generated'
import { Switch } from '@/components/ui/switch'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select'
import {
  componentFields, fieldHint, isShown, panelGroups,
  type RuleComponent, type RuleField, type RuleSystemManifest, type RuleValues,
} from '@/lib/ruleSchema'

type VillageRulesRead = components['schemas']['VillageRulesRead']
type HouseRulesPreview = components['schemas']['HouseRulesPreviewRead']
type HouseRulesExport = components['schemas']['HouseRulesExport']

/** 一条规则：标题与控件同行（控件一律靠右，右边缘天然对齐），说明另起一行占满宽度。
 *
 *  说明不能跟标题挤在左半边——那样它只剩一半宽度，一句话要折三行，整列看着像被压扁的表格。
 *  ``indent`` 用于从属项（幸运消费展开的那几条），靠缩进表明它们依附于上一条。 */
function Row({ label, hint, indent, children }: {
  label: React.ReactNode
  hint?: string
  indent?: boolean
  children: React.ReactNode
}) {
  return (
    <div className={`py-2 ${indent ? 'pl-3' : ''}`} style={
      indent ? { borderLeft: '2px solid var(--color-border)' } : undefined
    }>
      <div className="flex items-center justify-between gap-3">
        <span className="text-sm leading-tight">{label}</span>
        <span className="flex-shrink-0">{children}</span>
      </div>
      {hint && (
        <p className="mt-1 leading-snug" style={{
          fontSize: 'var(--text-2xs)', color: 'var(--color-text-secondary)',
        }}>
          {hint}
        </p>
      )}
    </div>
  )
}

function NumberBox({ label, value, onChange, min, max }: {
  label: string
  value: number
  onChange: (v: number) => void
  min: number
  max: number
}) {
  return (
    <input
      type="number"
      aria-label={label}
      value={value}
      min={min}
      max={max}
      onChange={(e) => onChange(Number(e.target.value))}
      className="input w-16 text-sm text-right"
    />
  )
}

const NOTES_MAX = 800

/** 一个参数的控件：布尔→开关，整数→数字框，枚举→下拉。区间、文案全来自后端 schema。 */
function FieldControl({ field, value, label, onChange }: {
  field: RuleField
  value: unknown
  label: string
  onChange: (v: unknown) => void
}) {
  if (field.kind === 'boolean') {
    return <Switch label={label} checked={Boolean(value)} onChange={onChange} />
  }
  if (field.kind === 'choice') {
    return (
      <Select value={String(value)} onValueChange={onChange}>
        <SelectTrigger className="w-[8.5rem] text-sm" aria-label={label}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {field.choices?.map((c) => <SelectItem key={c.value} value={c.value}>{c.label}</SelectItem>)}
        </SelectContent>
      </Select>
    )
  }
  return (
    <NumberBox label={label} value={Number(value)} min={field.min ?? 0} max={field.max ?? 999}
      onChange={onChange} />
  )
}

/** 一个组件在面板上的几行。
 *
 *  可选组件（有 toggle）：开关行用组件说明作提示，开着才展开它的其余参数（缩进表示从属）。
 *  常驻组件：逐个参数一行；带 x-show-if 的参数只在条件满足时出现，同样缩进。 */
function ComponentRows({ component, values, patch }: {
  component: RuleComponent
  values: RuleValues
  patch: (next: RuleValues) => void
}) {
  const fields = componentFields(component)
  const toggle = component.toggle ? fields.find((f) => f.key === component.toggle) : undefined
  const rest = fields.filter((f) => f !== toggle)
  const open = !toggle || Boolean(values[toggle.key])

  // 第三方插件提供的规则标出来：房主有权一眼看出哪条不是官方规则
  const fromPlugin = (component.source ?? 'official').startsWith('plugin:')
  const title = (f: RuleField, head: boolean) => (head && fromPlugin
    ? <>{f.title} <span className="chip" style={{ fontSize: 'var(--text-2xs)' }}>插件</span></>
    : f.title)

  const row = (f: RuleField, indent: boolean, hint?: string) => (
    <Row key={f.key} indent={indent} label={title(f, !indent)} hint={hint ?? fieldHint(f, values[f.key])}>
      <FieldControl
        field={f}
        value={values[f.key]}
        // 从属项的无障碍名带上组件名：「单次上限」单拎出来听不出是谁的上限
        label={indent && toggle ? `${component.title}·${f.title}` : f.title}
        onChange={(v) => patch({ [f.key]: v })}
      />
    </Row>
  )

  return (
    <>
      {toggle && row(toggle, false, component.description || toggle.hint)}
      {open && rest.filter((f) => isShown(f, values)).map((f) => row(f, Boolean(toggle || f.showIf)))}
    </>
  )
}

/** 导入预览：先看清这份房规改了什么、有没有问题，确认后才保存。 */
function ImportPreview({ preview, onConfirm, onCancel, busy }: {
  preview: HouseRulesPreview
  onConfirm: () => void
  onCancel: () => void
  busy: boolean
}) {
  const labels = preview.labels ?? []
  const warnings = preview.warnings ?? []
  return (
    <div className="rounded-md px-3 py-2.5 mb-3 text-sm" role="region" aria-label="房规导入预览"
      style={{ background: 'var(--color-bg-tertiary)', border: '1px solid var(--color-border)' }}>
      <div style={{ color: 'var(--color-text-primary)' }}>
        导入「{preview.name || '未命名房规'}」
      </div>
      {preview.description && (
        <div className="text-xs mt-0.5" style={{ color: 'var(--color-text-secondary)' }}>
          {preview.description}
        </div>
      )}

      {preview.ok ? (
        <div className="mt-2 text-xs" style={{ color: 'var(--color-text-secondary)' }}>
          {labels.length === 0
            ? '这份房规完全照规则书原文，没有改动。'
            : `与规则书原文有 ${labels.length} 处不同：`}
          {labels.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-1.5">
              {labels.map((t) => <span key={t} className="chip">{t}</span>)}
            </div>
          )}
          {preview.table_notes && <div className="mt-1.5">附带桌面约定：{preview.table_notes}</div>}
          <div className="mt-1.5">导入会<b>整份替换</b>当前的村规参数、数据表改动与桌面约定。</div>
        </div>
      ) : (
        <ul className="mt-2 text-xs list-disc pl-4" style={{ color: 'var(--color-danger)' }}>
          {(preview.errors ?? []).map((e) => <li key={e}>{e}</li>)}
        </ul>
      )}

      {warnings.length > 0 && (
        <ul className="mt-2 text-xs list-disc pl-4" style={{ color: 'var(--color-text-secondary)' }}>
          {warnings.map((w) => <li key={w}>{w}</li>)}
        </ul>
      )}

      <div className="flex justify-end gap-2 mt-2.5">
        <button onClick={onCancel} className="btn-secondary btn-sm">取消</button>
        {preview.ok && (
          <button onClick={onConfirm} disabled={busy} className="btn-primary btn-sm">
            {busy ? '导入中…' : '确认导入'}
          </button>
        )}
      </div>
    </div>
  )
}

export function VillageRulesPanel({ ruleSystem, twoColumn, onSaved }: {
  ruleSystem: string
  /** 宽容器里把参数排成两列：单列十一行要滚半屏，两列一屏就看完了，
   *  而且判定类与伤害/状态类天然分开（分栏取自各组件的 group），比一长串更有条理。 */
  twoColumn?: boolean
  /** 保存成功后回调——页面上方的规矩摘要靠它跟着刷新，否则会停在旧值上。 */
  onSaved?: () => void
}) {
  const [manifest, setManifest] = useState<RuleSystemManifest | null>(null)
  const [rules, setRules] = useState<RuleValues | null>(null)
  const [notes, setNotes] = useState('')
  const [enabled, setEnabled] = useState(true)
  const [saving, setSaving] = useState(false)
  const [preview, setPreview] = useState<HouseRulesPreview | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        const [data, m] = await Promise.all([
          api.get<VillageRulesRead>(`/rulebooks/village-rules/${ruleSystem}`),
          api.get<RuleSystemManifest>(`/rulebooks/village-rules/${ruleSystem}/manifest`),
        ])
        if (alive) {
          setRules(data.effective)
          setNotes(data.table_notes || '')
          setEnabled(data.enabled !== false)
          setManifest(m)
        }
      } catch (e: unknown) {
        toast.error(e instanceof Error ? e.message : '读取村规失败')
      }
    })()
    return () => { alive = false }
  }, [ruleSystem])

  const patch = (next: RuleValues) => setRules((cur) => (cur ? { ...cur, ...next } : cur))

  const persist = async (
    options: RuleValues, tableNotes: string, message: string, data?: Record<string, unknown>,
  ) => {
    setSaving(true)
    try {
      // 整份提交，后端负责白名单化、钳区间、只存与规则原文的差异。
      // data 只在导入房规时带（整份替换数据表改动）；平时保存参数不动数据表。
      const saved = await api.put<VillageRulesRead>(`/rulebooks/village-rules/${ruleSystem}`, {
        options, table_notes: tableNotes, enabled, ...(data ? { data } : {}),
      })
      if (saved?.effective) {
        setRules(saved.effective)
        setNotes(saved.table_notes ?? tableNotes)
      }
      toast.success(message)
      onSaved?.()
      return true
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : '保存失败')
      return false
    } finally {
      setSaving(false)
    }
  }

  const save = async () => {
    if (!rules) return
    await persist(
      rules, notes,
      enabled ? '村规已更新，下一次掷骰起生效' : '村规已停用，这一桌回到规则原文',
    )
  }

  const exportRules = async () => {
    try {
      const r = await api.get<HouseRulesExport>(`/rulebooks/village-rules/${ruleSystem}/export`)
      const url = URL.createObjectURL(new Blob([r.text], { type: 'application/x-yaml;charset=utf-8' }))
      const a = document.createElement('a')
      a.href = url
      a.download = r.filename
      a.click()
      URL.revokeObjectURL(url)
      toast.success('房规已导出（导出的是已保存的配置）')
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : '导出失败')
    }
  }

  const pickFile = async (file: File | undefined) => {
    if (!file) return
    try {
      const text = await file.text()
      setPreview(await api.post<HouseRulesPreview>(
        `/rulebooks/village-rules/${ruleSystem}/import-preview`, { text },
      ))
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : '读取房规文件失败')
    } finally {
      if (fileRef.current) fileRef.current.value = ''   // 同一个文件改完再选也要能触发
    }
  }

  const confirmImport = async () => {
    if (!preview) return
    const ok = await persist(
      preview.options ?? {}, preview.table_notes ?? '', `已导入「${preview.name || '房规'}」`,
      preview.data ?? {},
    )
    if (ok) setPreview(null)
  }

  if (!rules || !manifest) {
    return (
      <div className="text-sm py-6 text-center" style={{ color: 'var(--color-text-secondary)' }}>
        读取中…
      </div>
    )
  }

  const groups = panelGroups(manifest)

  return (
    <div>
      <div className="text-xs mb-3" style={{ color: 'var(--color-text-secondary)' }}>
        这一桌长期沿用的规则改动，对所有用这套规则的房间生效（含进行中的局）；不改的项照规则书原文来。
      </div>

      {preview && (
        <ImportPreview preview={preview} busy={saving}
          onConfirm={() => void confirmImport()} onCancel={() => setPreview(null)} />
      )}

      {/* 总开关。关掉时下面配好的东西一条都不生效，但也**不会被清掉**——
          想先照规则原文跑一局试试，回头一开就全回来了。 */}
      <div className="flex items-center justify-between gap-3 rounded-md px-3 py-2 mb-3"
        style={{ background: 'var(--color-bg-tertiary)', border: '1px solid var(--color-border)' }}>
        <div className="min-w-0">
          <div className="text-sm" style={{ color: 'var(--color-text-primary)' }}>启用村规</div>
          <div className="text-xs mt-0.5" style={{ color: 'var(--color-text-secondary)' }}>
            {enabled
              ? '下面配好的改动正在生效。'
              : '已停用：这一桌完全照规则书原文跑，下面的配置留着不动，随时能开回来。'}
          </div>
        </div>
        <Switch label="启用村规" checked={enabled} onChange={setEnabled} />
      </div>

      {/* 停用时把配置区压暗：仍然可读可改（改完开回来就生效），但一眼看得出它此刻不算数 */}
      <div style={enabled ? undefined : { opacity: 0.55 }}>

      <div className={twoColumn ? 'grid gap-x-6 md:grid-cols-2' : ''}>
        {groups.map((g) => (
          <div key={g.name} className="divide-y self-start" aria-label={g.name}
            style={{ borderColor: 'var(--color-border)' }}>
            {g.components.map((c) => (
              <ComponentRows key={c.id} component={c} values={rules} patch={patch} />
            ))}
          </div>
        ))}
      </div>

      {/* 桌面约定：参数表达不了的规矩走自由文本。**必须把界限写在界面上**——它进的是
          KP 的提示词，只改叙述与裁定倾向，不改任何一次骰子结算。不说清楚，玩家会在这里
          写「大失败只认 100」然后以为生效了。 */}
      <div className="mt-4 pt-3" style={{ borderTop: '1px solid var(--color-border)' }}>
        <label htmlFor="table-notes" className="text-sm">桌面约定</label>
        <p className="mt-1 mb-1.5 leading-snug" style={{
          fontSize: 'var(--text-2xs)', color: 'var(--color-text-secondary)',
        }}>
          参数管不到的规矩，写给守秘人看（「重调查轻战斗」「NPC 死亡不可逆」「别用现代词汇」）。
          <b>只影响怎么演，不改骰子结算</b>——要改判定请用上面的参数。
        </p>
        <textarea
          id="table-notes"
          value={notes}
          maxLength={NOTES_MAX}
          rows={4}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="例：本局重调查轻战斗，能谈就别打；NPC 死了就是死了，不要复活。"
          className="input w-full text-sm resize-y"
        />
        <div className="text-right" style={{
          fontSize: 'var(--text-2xs)', color: 'var(--color-text-secondary)',
        }}>
          {notes.length}/{NOTES_MAX}
        </div>
      </div>
      </div>

      {/* 房规文件：把这一桌的规矩发给别人，或用别人的。文件里只有数据，导入前先预览。 */}
      <div className="flex flex-wrap items-center justify-between gap-2 mt-3">
        <div className="flex gap-2">
          <button onClick={() => void exportRules()}
            className="btn-secondary btn-sm inline-flex items-center gap-1.5">
            <Download size={14} aria-hidden /> 导出房规
          </button>
          <button onClick={() => fileRef.current?.click()}
            className="btn-secondary btn-sm inline-flex items-center gap-1.5">
            <Upload size={14} aria-hidden /> 导入房规
          </button>
          <input ref={fileRef} type="file" accept=".yaml,.yml,text/yaml" className="hidden"
            aria-label="选择房规文件" onChange={(e) => void pickFile(e.target.files?.[0])} />
        </div>
        <button onClick={() => void save()} disabled={saving} className="btn-primary btn-sm">
          {saving ? '保存中…' : '保存村规'}
        </button>
      </div>
    </div>
  )
}
