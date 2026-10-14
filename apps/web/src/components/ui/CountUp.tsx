import { useEffect } from 'react'
import { animate, m, useMotionValue, useReducedMotion, useTransform } from 'motion/react'
import { ease } from '@/lib/motion'

/**
 * 从 0 滚到目标值的数字。用在「你手上有几位调查员」这类首屏统计上：数字是自己长出来的，
 * 比一开始就钉在那里更像一份刚被翻开的档案。
 *
 * 读屏与测试读的是隐藏的真值，滚动的那份 aria-hidden——动画中途的 3、5 不该被念出来。
 * 值变化时从**当前显示值**接着滚，而不是归零重来。
 */
export function CountUp({ value, className }: { value: number; className?: string }) {
  const reduce = useReducedMotion()
  const mv = useMotionValue(reduce ? value : 0)
  const shown = useTransform(mv, (v) => Math.round(v))

  useEffect(() => {
    if (reduce) {
      mv.set(value)
      return
    }
    const controls = animate(mv, value, { duration: 0.9, ease: ease.out })
    return () => controls.stop()
  }, [value, reduce, mv])

  return (
    <span className={className}>
      <span className="sr-only">{value}</span>
      <m.span aria-hidden>{shown}</m.span>
    </span>
  )
}
