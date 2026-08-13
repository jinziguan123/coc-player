import { useState } from 'react'
import { toast } from 'sonner'
import { Loader2, Pencil, RotateCcw, Sparkles, Trash2 } from 'lucide-react'
import { api } from '@/api/client'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { CharacterPanel } from '@/components/character/CharacterPanel'
import { CharacterEditModal } from '@/components/character/CharacterEditModal'

/** 与大厅页共用的最小角色形状；编辑弹窗要的字段是它的子集。 */
export interface TeammateDraft {
  id: string
  name: string
  base_attributes: Record<string, number>
  skills: Record<string, number>
  system_data: Record<string, unknown>
  backstory: string
  status: string
  avatar_url?: string | null
}

interface Props {
  open: boolean
  moduleId: string
  /** 关闭对话框（无论走的是哪条出口）。 */
  onClose: () => void
  /** 玩家确认保留：把这张卡指派到发起的那个席位。 */
  onConfirm: (char: TeammateDraft) => Promise<void> | void
}

/**
 * AI 队友的「写提示词 → 生成 → 过目/编辑 → 决定去留」。
 *
 * 一键直接生成并入座太粗暴：玩家既说不上想要什么，生成完也没机会看一眼就已经坐下了。
 * 这里拆成三步，中间那步是真正的把关点——卡先落库（编辑弹窗要 PUT 一个已存在的角色），
 * 但**不指派席位**；玩家弃用就连卡一起删掉，库里不留垃圾。
 */
export function AiTeammateDialog({ open, moduleId, onClose, onConfirm }: Props) {
  const [hint, setHint] = useState('')
  const [phase, setPhase] = useState<'hint' | 'generating' | 'review'>('hint')
  const [draft, setDraft] = useState<TeammateDraft | null>(null)
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState(false)

  const reset = () => { setHint(''); setPhase('hint'); setDraft(null); setEditing(false) }

  const generate = async () => {
    setPhase('generating')
    try {
      const spec = await api.post<Record<string, unknown>>('/characters/ai-generate', {
        module_id: moduleId, hint: hint.trim(), is_player: false,
      })
      const created = await api.post<TeammateDraft>('/characters', {
        name: spec.name, module_id: moduleId, rule_system: (spec.rule_system as string) || 'coc',
        is_player: false, age: spec.age ?? 25, base_attributes: spec.base_attributes,
        skills: spec.skills, system_data: spec.system_data, backstory: spec.backstory ?? '',
      })
      setDraft(created)
      setPhase('review')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'AI 生成队友失败')
      setPhase('hint')
    }
  }

  /** 删掉这张还没被采用的卡；失败只提示，不挡住流程。 */
  const discardDraft = async (d: TeammateDraft) => {
    try { await api.delete(`/characters/${d.id}`) } catch { /* 留在库里也不影响房间 */ }
  }

  const regenerate = async () => {
    if (!draft) return
    setBusy(true)
    await discardDraft(draft)
    setDraft(null)
    setBusy(false)
    setPhase('hint')      // 回到提示词那一步：重来往往是因为想换个说法
  }

  const discard = async () => {
    if (draft) { setBusy(true); await discardDraft(draft); setBusy(false) }
    reset()
    onClose()
  }

  const keep = async () => {
    if (!draft) return
    setBusy(true)
    try {
      await onConfirm(draft)
      reset()
      onClose()
    } finally { setBusy(false) }
  }

  return (
    <>
      <Dialog
        open={open}
        onOpenChange={(v) => {
          if (v) return
          // 生成中不许关：请求已经发出去，关掉只会让人以为取消了
          if (phase === 'generating') return
          // 过目阶段被 ESC/点遮罩关掉：保守处理——卡留在角色库，但不入座
          if (phase === 'review' && draft) {
            toast.info(`「${draft.name}」已存进角色库，但没有入座`)
          }
          reset()
          onClose()
        }}
      >
        <DialogContent
          className={phase === 'review' ? '!max-w-2xl flex max-h-[86vh] flex-col overflow-hidden' : ''}
        >
          <DialogHeader>
            <DialogTitle>
              {phase === 'review' ? '过目这张队友卡' : '生成 AI 队友'}
            </DialogTitle>
          </DialogHeader>

          {phase === 'hint' && (
            <div>
              <p className="mb-2 text-xs" style={{ color: 'var(--color-text-secondary)' }}>
                想要个什么样的队友？留空则由 AI 按本模组自由发挥。
              </p>
              <textarea
                value={hint}
                onChange={(e) => setHint(e.target.value)}
                rows={3}
                autoFocus
                placeholder="如 沉默寡言的退伍军医、油滑但消息灵通的记者、懂点神秘学的图书管理员"
                className="input mb-3 w-full resize-y text-sm"
              />
              <div className="flex items-center justify-end gap-2">
                <button onClick={() => { reset(); onClose() }} className="btn-secondary text-sm">
                  取消
                </button>
                <button onClick={() => void generate()} className="btn-primary inline-flex items-center gap-1 text-sm">
                  <Sparkles size={13} /> 生成
                </button>
              </div>
            </div>
          )}

          {phase === 'generating' && (
            <div className="flex flex-col items-center gap-3 py-8" style={{ color: 'var(--color-text-secondary)' }}>
              <Loader2 size={28} className="animate-spin" style={{ color: 'var(--color-text-accent)' }} />
              <p className="text-sm">正在按本模组生成队友卡…</p>
              {/* 实测一分多钟。不说清楚的话，这段静默会被当成卡死 */}
              <p className="text-xs">整张卡要现算属性、技能与背景，通常要一分钟左右</p>
            </div>
          )}

          {phase === 'review' && draft && (
            <div className="flex min-h-0 flex-1 flex-col">
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <span className="text-sm font-semibold" style={{ color: 'var(--color-text-accent)' }}>
                  {draft.name}
                </span>
                <span className="text-xs" style={{ color: 'var(--color-text-secondary)' }}>
                  还没有入座——看过、改过，再决定要不要它
                </span>
              </div>
              <div className="min-h-0 flex-1 overflow-y-auto">
                <CharacterPanel character={draft} />
              </div>
              <div className="mt-3 flex flex-wrap items-center gap-2 border-t pt-3" style={{ borderColor: 'var(--color-border)' }}>
                <button onClick={() => setEditing(true)} disabled={busy}
                  className="btn-secondary inline-flex items-center gap-1 text-sm">
                  <Pencil size={13} /> 编辑
                </button>
                <button onClick={() => void regenerate()} disabled={busy}
                  className="btn-secondary inline-flex items-center gap-1 text-sm">
                  <RotateCcw size={13} /> 重新生成
                </button>
                <button onClick={() => void discard()} disabled={busy}
                  className="btn-secondary inline-flex items-center gap-1 text-sm"
                  style={{ color: 'var(--color-danger)', borderColor: 'var(--color-danger)' }}>
                  <Trash2 size={13} /> 弃用
                </button>
                <button onClick={() => void keep()} disabled={busy} className="btn-primary ml-auto text-sm">
                  {busy ? '处理中…' : '保留并入座'}
                </button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* 编辑复用角色页那套弹窗（它 PUT 一个已存在的角色，所以卡必须先落库） */}
      {draft && (
        <CharacterEditModal
          character={draft}
          open={editing}
          onOpenChange={setEditing}
          onSaved={(c) => { setDraft(c as TeammateDraft); setEditing(false) }}
          onPatched={(c) => setDraft(c as TeammateDraft)}
        />
      )}
    </>
  )
}
