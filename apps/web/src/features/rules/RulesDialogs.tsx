// 规则页的两个对话框：导入房规（预览后载入草稿）与离开前的「还没保存」。
import { useRef, useState } from 'react'
import { Upload } from 'lucide-react'
import { toast } from 'sonner'
import { api } from '@/api/client'
import type { components } from '@/api/generated'
import { Modal, ModalPresence } from '@/components/ui/modal'
import type { Change } from '@/lib/ruleDraft'

type HouseRulesPreview = components['schemas']['HouseRulesPreviewRead']

export function ImportDialog({ ruleSystem, open, onClose, onApply }: {
  ruleSystem: string
  open: boolean
  onClose: () => void
  /** 把预览载入草稿（不保存） */
  onApply: (preview: HouseRulesPreview) => Promise<void>
}) {
  const fileRef = useRef<HTMLInputElement>(null)
  const [preview, setPreview] = useState<HouseRulesPreview | null>(null)
  const [busy, setBusy] = useState(false)

  const close = () => { setPreview(null); onClose() }

  const pick = async (file: File | undefined) => {
    if (!file) return
    try {
      const text = await file.text()
      setPreview(await api.post<HouseRulesPreview>(`/rulebooks/village-rules/${ruleSystem}/import-preview`, { text }))
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : '读取房规文件失败')
    } finally {
      if (fileRef.current) fileRef.current.value = ''   // 同一个文件改完再选也要能触发
    }
  }

  const apply = async () => {
    if (!preview) return
    setBusy(true)
    try {
      await onApply(preview)
      toast.success('已载入草稿：看看高亮的几处，没问题就保存')
      close()
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : '载入失败')
    } finally {
      setBusy(false)
    }
  }

  const labels = preview?.labels ?? []
  return (
    <ModalPresence>
      {open && (
        <Modal onClose={close} padded>
          <h3 className="card-title">导入房规</h3>
          {!preview ? (
            <>
              <p className="text-sm leading-relaxed" style={{ color: 'var(--color-text-secondary)' }}>
                选一份别人导出的房规文件（.yaml）。导入只会<b>载入草稿</b>，不会直接生效——你可以逐项看过、
                改掉不想要的，再保存。文件里只有数据，不会执行任何代码。
              </p>
              <div className="flex justify-end gap-2 mt-4">
                <button type="button" className="btn-secondary btn-sm" onClick={close}>取消</button>
                <button type="button" className="btn-primary btn-sm inline-flex items-center gap-1.5"
                  onClick={() => fileRef.current?.click()}>
                  <Upload size={13} aria-hidden /> 选择文件
                </button>
              </div>
            </>
          ) : (
            <>
              <div className="text-sm" style={{ color: 'var(--color-text-primary)' }}>「{preview.name || '未命名房规'}」</div>
              {preview.description && <div className="text-xs mt-0.5" style={{ color: 'var(--color-text-secondary)' }}>{preview.description}</div>}
              {preview.ok ? (
                <div className="mt-2 text-sm" style={{ color: 'var(--color-text-secondary)' }}>
                  {labels.length === 0 ? '这份房规完全照规则书原文，没有改动。' : `与规则书原文有 ${labels.length} 处不同：`}
                  {labels.length > 0 && (
                    <div className="flex flex-wrap gap-1.5 mt-1.5">{labels.map((t) => <span key={t} className="chip">{t}</span>)}</div>
                  )}
                  {preview.table_notes && <div className="mt-1.5">附带桌面约定：{preview.table_notes}</div>}
                  <div className="mt-2 text-xs">载入后会<b>整份替换</b>草稿里的规则、数据表与桌面约定。</div>
                </div>
              ) : (
                <ul role="alert" className="mt-2 text-xs list-disc pl-4" style={{ color: 'var(--color-danger)' }}>
                  {(preview.errors ?? []).map((e) => <li key={e}>{e}</li>)}
                </ul>
              )}
              {(preview.warnings ?? []).length > 0 && (
                <ul className="mt-2 text-xs list-disc pl-4" style={{ color: 'var(--color-text-secondary)' }}>
                  {(preview.warnings ?? []).map((w) => <li key={w}>{w}</li>)}
                </ul>
              )}
              <div className="flex justify-end gap-2 mt-4">
                <button type="button" className="btn-secondary btn-sm" onClick={close}>取消</button>
                {preview.ok && (
                  <button type="button" className="btn-primary btn-sm" onClick={() => void apply()} disabled={busy}>
                    {busy ? '载入中…' : '载入到草稿'}
                  </button>
                )}
              </div>
            </>
          )}
          <input ref={fileRef} type="file" accept=".yaml,.yml,text/yaml" className="hidden"
            aria-label="选择房规文件" onChange={(e) => void pick(e.target.files?.[0])} />
        </Modal>
      )}
    </ModalPresence>
  )
}

export function LeaveDialog({ pending, target, saving, onStay, onDiscard, onSave }: {
  pending: Change[]
  /** 想去的地址；null = 不显示 */
  target: string | null
  saving: boolean
  onStay: () => void
  onDiscard: () => void
  onSave: () => void
}) {
  return (
    <ModalPresence>
      {target !== null && (
        <Modal onClose={onStay} padded>
          <h3 className="card-title">还有 {pending.length} 处改动没保存</h3>
          <p className="text-sm" style={{ color: 'var(--color-text-secondary)' }}>离开后这些改动会丢失：</p>
          <ul className="mt-2 text-sm list-disc pl-5 leading-relaxed">
            {pending.slice(0, 6).map((c) => <li key={c.key}>{c.text}</li>)}
            {pending.length > 6 && <li>……等 {pending.length} 处</li>}
          </ul>
          <div className="flex justify-end gap-2 mt-4">
            <button type="button" className="btn-secondary btn-sm" onClick={onStay}>继续编辑</button>
            <button type="button" className="btn-secondary btn-sm" onClick={onDiscard}>放弃并离开</button>
            <button type="button" className="btn-primary btn-sm" onClick={onSave} disabled={saving}>
              {saving ? '保存中…' : '保存并离开'}
            </button>
          </div>
        </Modal>
      )}
    </ModalPresence>
  )
}
