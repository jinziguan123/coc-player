import { useState, type ReactNode } from 'react'
import * as PopoverPrimitive from '@radix-ui/react-popover'
import { MoreHorizontal } from 'lucide-react'

/**
 * 「更多」菜单。
 *
 * 对局页顶栏一度并排挂着九个同样大小的 btn-xs：检索、战报、结束模组、成长、大地图、
 * 临场角色、风格、导览、面板开合。它们性质完全不同（查看 / 局面控制 / 房主设置 / 视图），
 * 却长得一模一样、挤成一堵墙——要找哪个都得逐个读一遍。常用的留在外面，一局用不了一次
 * 的收进这里。AI 配置那份列表同理：每行七个 chip，收成一个主动作加这一个菜单。
 *
 * 浮层走 Radix Popover 的 Portal，不用 position:absolute。绝对定位的浮层会被祖先的
 * overflow 裁掉——AI 配置那份列表就在一个 `maxHeight + overflowY` 的滚动容器里，菜单
 * 展开后只露得出第一项，剩下的全被切在容器边界外。Portal 到 body 才躲得开。
 *
 * 语义仍是 menu / menuitem：Radix Popover 只管定位与开合，ARIA 角色这里自己给。
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

  if (items.length === 0) return null

  return (
    <PopoverPrimitive.Root open={open} onOpenChange={setOpen}>
      <PopoverPrimitive.Trigger
        className="btn-secondary btn-xs flex items-center gap-1"
        aria-haspopup="menu"
        aria-label={label}
        title={label}
      >
        <MoreHorizontal size={13} aria-hidden="true" />
      </PopoverPrimitive.Trigger>

      <PopoverPrimitive.Portal>
        <PopoverPrimitive.Content
          role="menu"
          align="end"
          sideOffset={6}
          // 留出边距，贴着视口边时 Radix 才会把它挪回来——最后一行的菜单本来会有一截
          // 探到视口外，摆在那儿等于看不见
          collisionPadding={12}
          className="more-menu-list z-[110]"
        >
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
        </PopoverPrimitive.Content>
      </PopoverPrimitive.Portal>
    </PopoverPrimitive.Root>
  )
}
