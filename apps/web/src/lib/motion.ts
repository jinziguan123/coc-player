/**
 * 动效节奏的单一真源（Motion 库）。
 *
 * 为什么引入 Motion 而不是继续手写 @keyframes：CSS 做得了「进场」，做不了三件事——
 * 已卸载元素的**退场**（下拉、弹窗、Tab 关掉就是瞬间消失）、跨组件的**共享元素过渡**
 * （首页拱门里的场景图 → 局内场景头）、以及会被打断还能自然衔接的**弹簧**。
 * 既有的 CSS 动画（消息入场、HP 受击、骰子等）保持原样，不做无谓迁移。
 *
 * 约定：组件里只用这里的预设，不在调用处随手写 `{ duration: 0.3 }`——
 * 全站节奏一致，才谈得上「流畅」，而不是每处各有各的快慢。
 * `prefers-reduced-motion` 由 main.tsx 的 <MotionConfig reducedMotion="user"> 统一兜住。
 */
import type { Transition, Variants } from 'motion/react'

/** 弹簧档位：按「物件的分量」选，而不是按时长。 */
export const spring = {
  /** 按钮、开关、指示条：干脆，几乎不过冲 */
  snappy: { type: 'spring', stiffness: 520, damping: 38, mass: 0.7 },
  /** 浮层、菜单、卡片：轻微回弹，有「弹出来」的手感 */
  pop: { type: 'spring', stiffness: 380, damping: 28, mass: 0.8 },
  /** 大块面板、页面：沉稳，不晃 */
  soft: { type: 'spring', stiffness: 220, damping: 30, mass: 1 },
  /** 黄铜表盘指针：有分量的金属件，明显过冲后回摆 */
  needle: { type: 'spring', stiffness: 140, damping: 11, mass: 1.1 },
} satisfies Record<string, Transition>

/** 非弹簧场景（淡入淡出、颜色）用的缓动：快出慢收。 */
export const ease = {
  out: [0.22, 1, 0.36, 1],
  inOut: [0.65, 0, 0.35, 1],
} as const

/** 路由切换：新页轻微上浮淡入；旧页快速淡出，不拖慢切页。 */
export const pageVariants: Variants = {
  initial: { opacity: 0, y: 10 },
  enter: { opacity: 1, y: 0, transition: { ...spring.soft, opacity: { duration: 0.24, ease: ease.out } } },
  exit: { opacity: 0, y: -4, transition: { duration: 0.12, ease: ease.inOut } },
}

/** 浮层（下拉、气泡、菜单）：从触发点方向缩放弹出，退场更快。 */
export const popoverVariants: Variants = {
  initial: { opacity: 0, scale: 0.94, y: -4 },
  enter: { opacity: 1, scale: 1, y: 0, transition: spring.pop },
  exit: { opacity: 0, scale: 0.97, y: -2, transition: { duration: 0.1, ease: ease.inOut } },
}

/** 悬浮窗遮罩：纯淡入淡出。退场与窗体同拍，别让遮罩比窗体多挂一截。 */
export const modalBackdropVariants: Variants = {
  initial: { opacity: 0 },
  enter: { opacity: 1, transition: { duration: 0.18, ease: ease.out } },
  exit: { opacity: 0, transition: { duration: 0.1, ease: ease.inOut } },
}

/** 悬浮窗窗体：比浮层更有分量——自下方微微浮起，退场略沉、比进场快得多。 */
export const modalPanelVariants: Variants = {
  initial: { opacity: 0, y: 8, scale: 0.985 },
  enter: { opacity: 1, y: 0, scale: 1, transition: { ...spring.pop, opacity: { duration: 0.16, ease: ease.out } } },
  exit: { opacity: 0, y: 4, scale: 0.985, transition: { duration: 0.1, ease: ease.inOut } },
}

/** 列表错峰：父容器用 listVariants，子项用 itemVariants。只给首屏那一批用（同 lib/stagger.ts 的约定）。 */
export const listVariants: Variants = {
  initial: {},
  enter: { transition: { staggerChildren: 0.045, delayChildren: 0.04 } },
}
export const itemVariants: Variants = {
  initial: { opacity: 0, y: 8 },
  enter: { opacity: 1, y: 0, transition: spring.soft },
}
