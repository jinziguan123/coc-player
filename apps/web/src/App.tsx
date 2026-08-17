import { lazy, Suspense } from 'react'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { AppShell } from './components/layout/AppShell'
import { Toaster } from './components/ui/toaster'
import './index.css'

// 路由级代码分割：把重页面（游戏会话、角色、模组详情、设置等）从首屏包中拆出，
// 避免一次下载整站。每页仍是命名导出，这里统一收敛成 default 给 React.lazy。
const HomePage = lazy(() => import('./pages/HomePage').then((m) => ({ default: m.HomePage })))
const ModulePage = lazy(() => import('./pages/ModulePage').then((m) => ({ default: m.ModulePage })))
const ModuleDetailPage = lazy(() => import('./pages/ModuleDetailPage').then((m) => ({ default: m.ModuleDetailPage })))
const RulebookPage = lazy(() => import('./pages/RulebookPage').then((m) => ({ default: m.RulebookPage })))
const CharacterPage = lazy(() => import('./pages/CharacterPage').then((m) => ({ default: m.CharacterPage })))
const GamePage = lazy(() => import('./pages/GamePage').then((m) => ({ default: m.GamePage })))
const GameSessionPage = lazy(() => import('./pages/GameSessionPage').then((m) => ({ default: m.GameSessionPage })))
const RoomLobbyPage = lazy(() => import('./pages/RoomLobbyPage').then((m) => ({ default: m.RoomLobbyPage })))
const SettingsPage = lazy(() => import('./pages/SettingsPage').then((m) => ({ default: m.SettingsPage })))
const OnboardingPage = lazy(() => import('./features/onboarding/OnboardingPage').then((m) => ({ default: m.OnboardingPage })))

function PageFallback() {
  return (
    <div className="flex min-h-[40vh] items-center justify-center text-sm" aria-live="polite">
      正在打开…
    </div>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <Toaster />
      <Suspense fallback={<PageFallback />}>
        <Routes>
          <Route element={<AppShell />}>
            <Route index element={<HomePage />} />
            <Route path="modules" element={<ModulePage />} />
            <Route path="modules/new" element={<ModuleDetailPage />} />
            <Route path="modules/:id" element={<ModuleDetailPage />} />
            <Route path="rulebooks" element={<RulebookPage />} />
            <Route path="characters" element={<CharacterPage />} />
            <Route path="game" element={<GamePage />} />
            <Route path="onboarding" element={<OnboardingPage />} />
            <Route path="room/:sessionId" element={<RoomLobbyPage />} />
            <Route path="game/:sessionId" element={<GameSessionPage />} />
            <Route path="settings" element={<SettingsPage />} />
          </Route>
        </Routes>
      </Suspense>
    </BrowserRouter>
  )
}
