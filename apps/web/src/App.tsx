import { lazy } from 'react'
import { BrowserRouter, Navigate, Routes, Route, useParams } from 'react-router-dom'
import { AppShell } from './components/layout/AppShell'
import { Toaster } from './components/ui/toaster'
import './index.css'

// 路由级代码分割：把重页面（游戏会话、角色、模组详情、设置等）从首屏包中拆出，
// 避免一次下载整站。每页仍是命名导出，这里统一收敛成 default 给 React.lazy。
const HomePage = lazy(() => import('./pages/HomePage').then((m) => ({ default: m.HomePage })))
const ModulePage = lazy(() => import('./pages/ModulePage').then((m) => ({ default: m.ModulePage })))
const ModuleDetailPage = lazy(() => import('./pages/ModuleDetailPage').then((m) => ({ default: m.ModuleDetailPage })))
const RulesPage = lazy(() => import('./pages/RulesPage').then((m) => ({ default: m.RulesPage })))
const CharacterPage = lazy(() => import('./pages/CharacterPage').then((m) => ({ default: m.CharacterPage })))
const GamePage = lazy(() => import('./pages/GamePage').then((m) => ({ default: m.GamePage })))
const GameSessionPage = lazy(() => import('./pages/GameSessionPage').then((m) => ({ default: m.GameSessionPage })))
const RoomLobbyPage = lazy(() => import('./pages/RoomLobbyPage').then((m) => ({ default: m.RoomLobbyPage })))
const SettingsPage = lazy(() => import('./pages/SettingsPage').then((m) => ({ default: m.SettingsPage })))
const OnboardingPage = lazy(() => import('./features/onboarding/OnboardingPage').then((m) => ({ default: m.OnboardingPage })))
// KP 值班台设计原型。只读页，与主流程互不影响；扶正过程中留着它做对照，最后再删。
const KpConsoleLab = lazy(() => import('./features/kp-lab/KpConsoleLab').then((m) => ({ default: m.KpConsoleLab })))

/**
 * 按存档号重挂游戏页。
 *
 * 同一条路由换参数时 React 会复用同一个组件实例，`/game/A` → `/game/B` 于是把上一局的
 * 战斗态、待决检定、正在看的角色卡、已知地点等几十个 state 原样带进下一局。这些状态没有
 * 一个是跨存档通用的，与其在页面里逐个手动清空（漏一个就是一处串味），不如让它按存档重挂。
 */
function GameSessionRoute() {
  const { sessionId } = useParams<{ sessionId: string }>()
  return <GameSessionPage key={sessionId} />
}

export default function App() {
  return (
    <BrowserRouter>
      <Toaster />
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<HomePage />} />
          <Route path="modules" element={<ModulePage />} />
          <Route path="modules/new" element={<ModuleDetailPage />} />
          <Route path="modules/:id" element={<ModuleDetailPage />} />
          <Route path="rules" element={<RulesPage />} />
          {/* 规则书并进了「规则」页的标签；旧地址（书签、旧链接）转过去 */}
          <Route path="rulebooks" element={<Navigate to="/rules?tab=books" replace />} />
          <Route path="characters" element={<CharacterPage />} />
          <Route path="game" element={<GamePage />} />
          <Route path="onboarding" element={<OnboardingPage />} />
          <Route path="room/:sessionId" element={<RoomLobbyPage />} />
          <Route path="game/:sessionId" element={<GameSessionRoute />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="lab/kp" element={<KpConsoleLab />} />
          <Route path="lab/kp/:sessionId" element={<KpConsoleLab />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
