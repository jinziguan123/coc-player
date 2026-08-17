import { useEffect, useRef } from 'react'
import { toast } from 'sonner'
import { Loader2 } from 'lucide-react'
import { localApi } from '@/api/client'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { CharacterEditModal } from '@/components/character/CharacterEditModal'

/** CharacterEditModal 需要的最小形状；草稿由大厅页负责创建。 */
export interface QuickDraft {
  id: string
  name: string
  base_attributes: Record<string, number>
  skills: Record<string, number>
  system_data: Record<string, unknown>
  backstory: string
  status: string
  avatar_url?: string | null
}

interface QuickCharacterCreateDialogProps {
  open: boolean
  /** 已创建但尚未保存的空白草稿；null 表示仍在创建中。 */
  draft: QuickDraft | null
  creating: boolean
  onClose: () => void
  /** 保存成功后把角色 id 交给大厅：刷新候选池并进入预览。 */
  onCreated: (characterId: string) => void
}

/**
 * 大厅里的「快速创建角色卡」：大厅页先 POST 一张空白草稿，这里复用角色页的
 * 编辑弹窗完成填写。编辑窗口没保存就被关掉时，把空草稿删掉，不在角色库里留垃圾。
 */
export function QuickCharacterCreateDialog({
  open, draft, creating, onClose, onCreated,
}: QuickCharacterCreateDialogProps) {
  /** 保存回调先于 CharacterEditModal 的 onOpenChange(false) 执行，用 ref 拦住「删掉刚保存的卡」。 */
  const savedRef = useRef(false)

  useEffect(() => {
    if (!open || !draft) savedRef.current = false
  }, [open, draft])

  const handleOpenChange = (next: boolean) => {
    if (next) return
    if (savedRef.current) return
    if (draft) {
      // 没保存就关掉：空草稿删掉。失败只提示，不挡住关闭。
      localApi.delete(`/characters/${draft.id}`).catch(() => {
        toast.error('未保存的角色草稿已留在角色库中，可在角色页删除')
      })
    }
    onClose()
  }

  return (
    <>
      <Dialog
        open={open && !draft}
        onOpenChange={(next) => { if (!next && !creating) onClose() }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>正在准备空白角色卡…</DialogTitle>
          </DialogHeader>
          <div className="flex items-center gap-2 text-sm" style={{ color: 'var(--color-text-secondary)' }}>
            <Loader2 size={16} className="animate-spin" style={{ color: 'var(--color-text-accent)' }} />
            创建后即可在编辑表单中填写属性、技能与背景。
          </div>
        </DialogContent>
      </Dialog>

      {draft && (
        <CharacterEditModal
          character={draft}
          open={open}
          onOpenChange={handleOpenChange}
          saveWith={(characterId, payload) =>
            localApi.put<QuickDraft>(`/characters/${characterId}`, payload)
          }
          onSaved={(updated) => {
            savedRef.current = true
            toast.success(`「${updated.name}」已保存到角色库`)
            onCreated(updated.id)
          }}
        />
      )}
    </>
  )
}
