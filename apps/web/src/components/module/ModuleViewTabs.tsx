import type { ReactNode } from 'react'
import { m } from 'motion/react'
import { spring } from '@/lib/motion'

export type ModuleView = 'detail' | 'graph' | 'timeline' | 'sandbox' | 'rules'

/** 模组详情的视图切换：分段控件，当前项的黄铜底在各项之间滑动 */
export function ModuleViewTabs({ view, items, onPick }: {
  view: ModuleView
  items: Array<{ id: ModuleView; icon: ReactNode; label: string }>
  onPick: (v: ModuleView) => void
}) {
  return (
    <div className="module-view-tabs">
      {items.map(({ id, icon, label }) => (
        <button
          key={id}
          aria-pressed={view === id}
          onClick={() => onPick(id)}
          className={`module-view-tab ${view === id ? 'module-view-tab--on' : ''}`}
        >
          {view === id && <m.span layoutId="module-view-active" className="module-view-tab-bg" transition={spring.snappy} />}
          {icon} {label}
        </button>
      ))}
    </div>
  )
}
