import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { LazyMotion, MotionConfig } from 'motion/react'
import './index.css'
// 手机适配层必须排在 index.css 之后：这些文件只写 <=767px 的覆盖规则，
// 依赖级联顺序压过桌面样式（CSS 的 @import 必须写在文件最前，保证不了这一点）。
import './styles/mobile/base.css'
import './styles/mobile/content.css'
import './styles/mobile/game.css'
import './styles/mobile/settings.css'
import App from './App.tsx'
import { ErrorBoundary } from './components/ErrorBoundary.tsx'

const loadMotionFeatures = () => import('./lib/motionFeatures').then((m) => m.default)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      {/* reducedMotion="user"：系统开了「减少动态效果」时，所有 Motion 动画退化为瞬时切换 */}
      <MotionConfig reducedMotion="user">
        <LazyMotion features={loadMotionFeatures} strict>
          <App />
        </LazyMotion>
      </MotionConfig>
    </ErrorBoundary>
  </StrictMode>,
)
