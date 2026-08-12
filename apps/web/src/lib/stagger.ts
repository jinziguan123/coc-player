import type { CSSProperties } from 'react'

/** 相邻两项的入场间隔。50ms 是「看得出先后、又不觉得在等」的那一档。 */
const STEP_MS = 45
/**
 * 累计延迟上限。长列表若按 index 一路乘下去，第 30 项要等 1.35s——用户早已在看它了，
 * 却还是空的，那不叫高级感叫卡。超过上限的项一律用同一个延迟，观感上就是「后半页一起浮现」。
 */
const MAX_DELAY_MS = 320

/**
 * 列表项错峰入场的行内样式。配合 `.list-enter`（见 index.css）使用：
 *
 * ```tsx
 * <div className={enterCls} style={staggerStyle(i)}>…</div>
 * ```
 *
 * **只该用在「首屏已有的那批」**。搜索、筛选、排序后的重排不要加——每敲一个字整页重新
 * 弹入是明确的倒退（同一条约定也写在 .list-enter 的注释里，与聊天流「历史不弹入」同源：
 * 动效标记的是「新出现的东西」，不是「重新排布的东西」）。
 */
export function staggerStyle(index: number): CSSProperties {
  const delay = Math.min(index * STEP_MS, MAX_DELAY_MS)
  return { '--enter-delay': `${delay}ms` } as CSSProperties
}

/**
 * 判断这一批是否该播入场动画：只在**首次拿到数据**时播一次。
 *
 * 传入当前的加载/筛选标识；与上一次不同就说明是用户在筛选，返回 false。
 * 组件里通常这样用：`const animate = useFirstPaint(items.length > 0)`。
 */
export function staggerClass(shouldAnimate: boolean): string {
  return shouldAnimate ? 'list-enter' : ''
}
