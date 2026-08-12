import { createPortal } from 'react-dom'
import { useEffect } from 'react'
import { GiCastle, GiEntryDoor } from 'react-icons/gi'

/**
 * 「新增游戏」的入口分岔：全屏遮罩 + 两张大卡片。
 *
 * 从前这两条路是同屏堆着的两个表单块（新游戏 + 加入房间），页面一打开就是一长条，
 * 而用户此刻只想回答一个问题：**我是开一局，还是去别人那局**。先把这个问题问清楚，
 * 后面的表单才只出现该出现的那一半。
 *
 * portal 到 body：GamePage 在 `.route-fade` 的 <main> 里，那上面有 transform/will-change，
 * 会给 fixed 定位建立新的包含块，遮罩会被裁在内容区里（与 components/ui/modal 同一处理）。
 */
export function GameEntryChooser({
  open, onCreate, onJoin, onClose,
}: {
  open: boolean
  onCreate: () => void
  onJoin: () => void
  onClose: () => void
}) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  return createPortal(
    <div
      className="entry-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label="选择开始方式"
      onClick={onClose}
    >
      <div className="entry-choices" onClick={(e) => e.stopPropagation()}>
        <button className="entry-card" onClick={onCreate} style={{ '--enter-delay': '40ms' } as React.CSSProperties}>
          <GiCastle aria-hidden="true" />
          <strong>创建房间</strong>
          <span>选一个模组开新局，人数与角色进房间再定</span>
        </button>
        <button className="entry-card" onClick={onJoin} style={{ '--enter-delay': '110ms' } as React.CSSProperties}>
          <GiEntryDoor aria-hidden="true" />
          <strong>加入房间</strong>
          <span>用邀请码或主机地址，进别人开好的房间</span>
        </button>
      </div>
      <button className="entry-dismiss" onClick={onClose}>取消</button>
    </div>,
    document.body,
  )
}
