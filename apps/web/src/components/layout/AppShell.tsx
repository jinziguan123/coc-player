import { Outlet, useLocation } from 'react-router-dom'
import { useKnockNotices } from '@/features/netlink/useKnockNotices'
import { Sidebar } from './Sidebar'

export function AppShell() {
  // 内置直连的敲门提示挂在这里：房主多半正在跑团而不是待在设置页，
  // 不全局提示的话，朋友会在门外干等到超时而他全程不知情。
  useKnockNotices()
  // 路由切换整页淡入（150ms）：用 pathname 作 key，切页即重挂载触发 route-fade。
  const { pathname } = useLocation()
  const gameSession = pathname.startsWith('/game/')
  return (
    <div className={`app-shell flex h-screen overflow-hidden ${gameSession ? 'game-session-shell' : ''}`}>
      <Sidebar />
      <main key={pathname} className="app-main min-w-0 flex-1 overflow-auto p-6 route-fade">
        <Outlet />
      </main>
    </div>
  )
}
