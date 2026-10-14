import type { HTMLAttributes, PointerEvent, ReactNode } from 'react'
import { m, useMotionTemplate, useMotionValue, useReducedMotion, useSpring, useTransform } from 'motion/react'
import { spring } from '@/lib/motion'

/**
 * 随指针微微倾斜的卡片：像把一张硬卡纸拿在手里转向灯光。
 *
 * - 倾角上限 `max` 度，弹簧跟随，移开后回正；
 * - 一道柔光（.tilt-shine）跟着指针走，读作「黄铜表面的反光」；
 * - 入场：从下方带一点前倾翻起（`index` 决定错峰）。只给首屏那一批，
 *   搜索/筛选后的重排不要再翻一遍（同 lib/stagger.ts 的约定）——传 `enter={false}`。
 *
 * 系统开了「减少动态效果」时既不倾斜也不翻入。
 */
export function TiltCard({
  children, className = '', max = 6, index = 0, enter = true, ...rest
}: {
  children: ReactNode
  className?: string
  max?: number
  index?: number
  enter?: boolean
} & Omit<HTMLAttributes<HTMLDivElement>, 'onDrag' | 'onDragStart' | 'onDragEnd' | 'onAnimationStart'>) {
  const reduce = useReducedMotion()
  const px = useMotionValue(0.5)
  const py = useMotionValue(0.5)
  const rx = useSpring(useTransform(py, [0, 1], [max, -max]), spring.soft)
  const ry = useSpring(useTransform(px, [0, 1], [-max, max]), spring.soft)
  const shineX = useTransform(px, (v) => `${v * 100}%`)
  const shineY = useTransform(py, (v) => `${v * 100}%`)
  const shine = useMotionTemplate`radial-gradient(circle at ${shineX} ${shineY}, rgba(230, 201, 138, 0.14), transparent 55%)`

  const onMove = (e: PointerEvent<HTMLDivElement>) => {
    if (reduce || e.pointerType !== 'mouse') return
    const r = e.currentTarget.getBoundingClientRect()
    px.set((e.clientX - r.left) / r.width)
    py.set((e.clientY - r.top) / r.height)
  }
  const onLeave = () => {
    px.set(0.5)
    py.set(0.5)
  }

  // 两层：外层只管入场翻起，内层只管随指针倾斜——同一个元素上 rotateX 既做动画又绑
  // motion value 会互相覆盖
  return (
    <m.div
      className="tilt-card-enter"
      initial={enter && !reduce ? { opacity: 0, y: 28, rotateX: -14 } : false}
      animate={{ opacity: 1, y: 0, rotateX: 0 }}
      transition={{ ...spring.soft, delay: enter ? Math.min(index * 0.06, 0.42) : 0 }}
      style={{ transformPerspective: 900 }}
    >
      <m.div
        {...rest}
        className={`tilt-card ${className}`}
        style={reduce ? undefined : { rotateX: rx, rotateY: ry, transformPerspective: 900 }}
        onPointerMove={onMove}
        onPointerLeave={onLeave}
      >
        {children}
        {!reduce && <m.span className="tilt-shine" aria-hidden style={{ background: shine }} />}
      </m.div>
    </m.div>
  )
}
