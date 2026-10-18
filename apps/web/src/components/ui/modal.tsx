import { useEffect, useLayoutEffect, useState, type CSSProperties, type MouseEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { AnimatePresence, m, useIsPresent } from 'motion/react'
import { modalBackdropVariants, modalPanelVariants } from '@/lib/motion'

/**
 * 通用悬浮窗：portal 到 document.body，避开路由转场的 <main>（动画期间带 transform）
 * 造成的「fixed 被限制在内容区」陷阱——遮罩因此能盖住整个视口（含左侧菜单栏）。
 * 窗体以**主内容区（<main>，即游戏聊天界面）**为中心居中（避开侧栏偏移）；不透明。
 *
 * 退场动画：调用方是 `{open && <Modal/>}` 式条件渲染，组件自己看不到「要被关掉了」。
 * 把条件渲染包进 <ModalPresence> 即可——它让被移除的那一份多留约 100ms 播完退场。
 * 包在外面而不是给 Modal 加 `open` 属性，是因为不少弹窗是带自身状态的包装组件
 * （RecapModal、SessionStyleModal…），整个组件连同内容都该原样冻住再退场，
 * 而不是常驻挂载、关着也在拉数据。没包 ModalPresence 时行为与从前一致（瞬间消失）。
 */
export function Modal({
  onClose,
  children,
  widthClass = 'max-w-xl',
  align = 'center',
  padded = false,
  /** 追加到窗体上的类名：给需要「固定标题/底部、只滚中间」的大表单用。 */
  panelClassName,
  /** 与类名配合的内联样式（如 display:flex）。合并顺序在底色之后，可安全覆盖。 */
  panelStyle,
}: {
  onClose: () => void
  children: ReactNode
  widthClass?: string
  align?: 'center' | 'top'
  padded?: boolean
  panelClassName?: string
  panelStyle?: CSSProperties
}) {
  // 退场途中（ModalPresence 还留着它播动画）：不再响应 Esc / 遮罩，点击直接穿透到底下页面
  const isPresent = useIsPresent()
  // 居中锚定到 <main> 左边缘：侧栏可折叠、宽度不固定，实测其 rect 比硬编码偏移可靠。
  const [left, setLeft] = useState(0)
  useLayoutEffect(() => {
    const measure = () => {
      const m = document.querySelector('main')
      setLeft(m ? m.getBoundingClientRect().left : 0)
    }
    measure()
    window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [])

  useEffect(() => {
    if (!isPresent) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, isPresent])

  const dismissOnBackdrop = (event: MouseEvent<HTMLDivElement>) => {
    if (isPresent && event.button === 0 && event.target === event.currentTarget) onClose()
  }

  // Dismiss on mousedown target only. If the panel recenters after content changes
  // (e.g. a tab switch), a click can be dispatched on the backdrop even though the
  // press started inside; the mousedown target is captured before that reflow.
  return createPortal(
    <m.div
      className="fixed inset-0 z-[100] modal-backdrop"
      variants={modalBackdropVariants}
      initial="initial"
      animate="enter"
      exit="exit"
      style={isPresent ? undefined : { pointerEvents: 'none' }}
      onMouseDown={dismissOnBackdrop}
    >
      {/* 定位层用 CSS 变量而不是写死 10vh/86vh：手机上要按 100dvh 与安全区收
          （见 styles/mobile/settings.css），桌面仍取 10vh / 86vh 原值。 */}
      <div
        className={`modal-positioner fixed flex justify-center ${align === 'top' ? 'items-start' : 'items-center'}`}
        style={{ top: 0, bottom: 0, left, right: 0, paddingTop: align === 'top' ? 'var(--modal-top-offset, 10vh)' : undefined }}
        onMouseDown={dismissOnBackdrop}
      >
        <m.div
          variants={modalPanelVariants}
          className={`modal-panel w-full ${widthClass} mx-4 rounded-lg overflow-hidden ${padded ? 'p-5' : ''}${panelClassName ? ` ${panelClassName}` : ''}`}
          style={{
            // 双层背景保证**不透明**（浅色主题 --color-bg-card 带 alpha）：不透明底 + 卡片色
            background: 'var(--color-bg-tertiary)',
            backgroundImage: 'linear-gradient(var(--color-bg-card), var(--color-bg-card))',
            maxHeight: 'var(--modal-max-height, 86vh)',
            ...panelStyle,
          }}
          onClick={(e) => e.stopPropagation()}
        >
          {children}
        </m.div>
      </div>
    </m.div>,
    document.body,
  )
}

/**
 * 给条件渲染的弹窗补退场：`<ModalPresence>{open && <Modal …/>}</ModalPresence>`。
 * 里面每个直接子元素须有唯一 key（只放一个时可省）。
 */
export function ModalPresence({ children }: { children: ReactNode }) {
  return <AnimatePresence>{children}</AnimatePresence>
}
