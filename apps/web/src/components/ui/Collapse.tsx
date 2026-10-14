import type { ReactNode } from 'react'
import { AnimatePresence, m } from 'motion/react'
import { spring } from '@/lib/motion'

/**
 * 高度展开 / 收起：内容从 0 长到自身高度，收起时反过来，而不是瞬间出现消失。
 * 外层 overflow:hidden 只在动画期间生效（展开完成后放开，不裁掉内部的下拉、阴影）。
 */
export function Collapse({ open, children, className = '' }: { open: boolean; children: ReactNode; className?: string }) {
  return (
    <AnimatePresence initial={false}>
      {open && (
        <m.div
          key="collapse"
          className={className}
          initial={{ height: 0, opacity: 0, overflow: 'hidden' }}
          animate={{ height: 'auto', opacity: 1, transitionEnd: { overflow: 'visible' } }}
          exit={{ height: 0, opacity: 0, overflow: 'hidden' }}
          transition={{ height: spring.soft, opacity: { duration: 0.2 } }}
        >
          {children}
        </m.div>
      )}
    </AnimatePresence>
  )
}
