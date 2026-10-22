import { Suspense } from 'react'
import { useOutlet, useLocation } from 'react-router-dom'
import { AnimatePresence, m } from 'motion/react'
import { useKnockNotices } from '@/features/netlink/useKnockNotices'
import { useNetlinkAutoStart } from '@/features/netlink/useNetlinkAutoStart'
import { pageVariants } from '@/lib/motion'
import { MobileNav } from './MobileNav'
import { Sidebar } from './Sidebar'
import { UploadJobsDrawer } from './UploadJobsDrawer'
import { useUploadJobsSync } from './useUploadJobsSync'

export function AppShell() {
  // 内置直连的这两件事都必须挂在全局，房主多半正在跑团而不是待在设置页：
  // 敲门提示不全局，朋友会在门外干等到超时而他全程不知情；
  // 自动恢复不全局，隧道要等他恰好翻到设置页才启动，客人在那之前怎么都进不来。
  useNetlinkAutoStart()
  useKnockNotices()
  // 模组解析进度同样挂全局：刷新页面后找回进行中的任务，完成时无论身在哪页都会提示
  useUploadJobsSync()
  const { pathname } = useLocation()
  // 退场动画期间旧页还要再渲染一会儿：用 useOutlet 拿到「这一刻」的页面元素交给
  // AnimatePresence 冻住，而不是 <Outlet />——后者会在旧页上直接渲染出新路由的内容。
  const outlet = useOutlet()
  const gameSession = pathname.startsWith('/game/')
  // 对局页在手机上要留出聊天输入区，不渲染底部导航；其余一级页面用底部导航替代侧栏。
  // （桌面仍渲染它，但 CSS 只在 <=767px 显示，不影响桌面。）
  const showMobileNav = !gameSession
  return (
    <div className={`app-shell flex h-dvh overflow-hidden ${gameSession ? 'game-session-shell' : ''} ${showMobileNav ? 'has-mobile-nav' : 'no-mobile-nav'}`}>
      <Sidebar autoCollapse={gameSession} />
      <AnimatePresence mode="wait" initial={false}>
        <m.main
          key={pathname}
          className="app-main min-w-0 flex-1 overflow-auto p-6"
          variants={pageVariants}
          initial="initial"
          animate="enter"
          exit="exit"
        >
          {/* Suspense 放在内容区里而不是整个壳外面：懒加载的页面还在下载时，
              侧栏照常在，只有内容区显示「正在打开」，而不是整屏被替换。 */}
          <Suspense fallback={<PageFallback />}>{outlet}</Suspense>
        </m.main>
      </AnimatePresence>
      {showMobileNav && <MobileNav />}
      <UploadJobsDrawer />
      <div className="film-grain" aria-hidden />
    </div>
  )
}

function PageFallback() {
  return (
    <div className="flex min-h-[40vh] items-center justify-center text-sm" aria-live="polite">
      正在打开…
    </div>
  )
}
