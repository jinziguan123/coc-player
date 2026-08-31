import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { MoreHorizontal } from 'lucide-react'

/**
 * 顶栏的「更多」菜单。
 *
 * 对局页顶栏一度并排挂着九个同样大小的 btn-xs：检索、战报、结束模组、成长、大地图、
 * 临场角色、风格、导览、面板开合。它们性质完全不同（查看 / 局面控制 / 房主设置 / 视图），
 * 却长得一模一样、挤成一堵墙——要找哪个都得逐个读一遍。
 *
 * 常用的留在外面，一局用不了一次的收进这里。
 */
export interface MoreMenuItem {
  label: string
  icon?: ReactNode
  onClick: () => void
  /** 悬停说明；菜单项本身写得够清楚时可以不给 */
  title?: string
  /** 分组之间画一道细线（放在该组第一项上） */
  separated?: boolean
}

export function MoreMenu({ items, label = '更多' }: {
  items: MoreMenuItem[]
  label?: string
}) {
  const [open, setOpen] = useState(false)
  const wrapRef = useRef<HTMLDivElement>(null)
  const menuId = useId()

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  if (items.length === 0) return null

  return (
    <div ref={wrapRef} className="more-menu">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="btn-secondary btn-xs flex items-center gap-1"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-label={label}
        title={label}
      >
        <MoreHorizontal size={13} aria-hidden="true" />
      </button>

      {open && (
        <div id={menuId} role="menu" className="more-menu-list">
          {items.map((item) => (
            <button
              key={item.label}
              type="button"
              role="menuitem"
              title={item.title}
              className={`more-menu-item${item.separated ? ' more-menu-item--sep' : ''}`}
              // 点完就收：这些都是「打开一个面板」或「发起一次操作」，没有连点的用法
              onClick={() => { setOpen(false); item.onClick() }}
            >
              {item.icon && <span className="more-menu-icon" aria-hidden="true">{item.icon}</span>}
              {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
