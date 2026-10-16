import { useState } from 'react'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { CharacterSheet } from '@/features/characters/sheet/CharacterSheet'
import { freshSheet } from '@/features/characters/sheet/useSheet'

/**
 * 大厅里的「快速创建角色卡」：弹窗里嵌一张一页式人物卡，默认规则书的「快速车卡」——
 * 八个定值分给属性、本职技能取九个定值、四项兴趣 +20，五分钟出卡；想细抠也能切到掷骰 / 点数购买。
 *
 * 与角色页同一套规则、同一个后端校验。卡在点「创建」时才落库，关掉弹窗不会在角色库里留空卡。
 */
export function QuickCharacterCreateDialog({ open, moduleId, onClose, onCreated }: {
  open: boolean
  /** 本机建卡时带上大厅的模组（给车卡建议、叠模组数据）；连主机时为空——那个模组不在本机库里 */
  moduleId: string | null
  onClose: () => void
  /** 建好之后把角色 id 交给大厅：刷新候选池并进入预览 */
  onCreated: (characterId: string) => void
}) {
  const [initial] = useState(() => ({ ...freshSheet('quick'), moduleId: moduleId ?? '' }))
  return (
    <Dialog open={open} onOpenChange={(next) => { if (!next) onClose() }}>
      <DialogContent className="!max-w-[min(96vw,1280px)]">
        <DialogHeader><DialogTitle>快速创建调查员</DialogTitle></DialogHeader>
        <div className="flex flex-col" style={{ height: 'min(80vh, 860px)' }}>
          <CharacterSheet initial={initial} saveDraft={false} cancelLabel="取消"
            onCancel={onClose} onCreated={(c) => onCreated(c.id)} />
        </div>
      </DialogContent>
    </Dialog>
  )
}
