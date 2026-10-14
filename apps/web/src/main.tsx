import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { LazyMotion, MotionConfig } from 'motion/react'
import './index.css'
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
