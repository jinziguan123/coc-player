import { useCallback, useEffect, useState } from 'react'
import { LoaderCircle, RefreshCw, Settings } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { checkAIStatus, startOnboarding } from './api'

type OnboardingState = 'checking' | 'needs_config' | 'creating' | 'error'

export function OnboardingPage() {
  const navigate = useNavigate()
  const [state, setState] = useState<OnboardingState>('checking')

  const run = useCallback(async () => {
    setState('checking')
    try {
      const status = await checkAIStatus()
      if (!status.configured) {
        setState('needs_config')
        return
      }

      setState('creating')
      const session = await startOnboarding()
      navigate(`/game/${session.session_id}`, { state: { isNew: true }, replace: true })
    } catch {
      setState('error')
    }
  }, [navigate])

  useEffect(() => {
    void run()
  }, [run])

  return (
    <main className="mx-auto flex min-h-[60vh] max-w-xl flex-col items-center justify-center px-4 text-center">
      {(state === 'checking' || state === 'creating') && (
        <>
          {/* 状态图标收进纹章框（与空态/入口卡同一语言），不再裸挂 */}
          <span className="empty-state-icon mb-5" aria-hidden="true">
            <LoaderCircle className="h-6 w-6 animate-spin" />
          </span>
          <h1 className="page-title">
            {state === 'checking' ? '正在检查 AI 配置' : '正在准备新手团'}
          </h1>
          <p style={{ color: 'var(--color-text-secondary)' }}>
            {state === 'checking' ? '确认模型可用后会自动继续。' : '正在准备原创模组与预设调查员。'}
          </p>
        </>
      )}

      {state === 'needs_config' && (
        <>
          <span className="empty-state-icon mb-5" aria-hidden="true">
            <Settings className="h-6 w-6" />
          </span>
          <h1 className="page-title">先接一个 AI 模型</h1>
          <p className="mb-5" style={{ color: 'var(--color-text-secondary)' }}>
            守秘人（KP）由 AI 担任，所以得先给它接一个模型。
            全程在本机完成，剧本与存档都不上传。
          </p>
          {/* 此前这里只有一句「需要配置 AI」+ 一个按钮——新用户到设置页面对一堆字段，
              并不知道该填什么、去哪拿密钥。这里把最短路径直接写出来。 */}
          <ol className="onboarding-steps">
            <li>
              到任一模型服务商拿一个 <strong>API Key</strong>。
              国内可用 DeepSeek，海外可用 OpenAI / Anthropic；
              本机跑 Ollama 也行（无需密钥）。
            </li>
            <li>
              点下面的按钮进设置，选「<strong>新增配置</strong>」。
            </li>
            <li>
              只需填四项：<strong>配置名称</strong>（随便起）、
              <strong>API 协议</strong>（DeepSeek / OpenAI / Ollama 都选「OpenAI 兼容」）、
              <strong>Base URL</strong> 与 <strong>API Key</strong>，
              再填<strong>模型名称</strong>（如 <code>deepseek-chat</code>）。
            </li>
            <li>
              保存后点「<strong>测试</strong>」确认通了，再点「<strong>激活</strong>」。
              回到这里就会自动继续。
            </li>
          </ol>
          <button
            className="btn-primary flex items-center gap-2"
            onClick={() => navigate('/settings', { state: { returnTo: '/onboarding' } })}
          >
            <Settings className="h-4 w-4" aria-hidden="true" />
            去设置里新增配置
          </button>
        </>
      )}

      {state === 'error' && (
        <>
          <span className="empty-state-icon mb-5" aria-hidden="true">
            <RefreshCw className="h-6 w-6" />
          </span>
          <h1 className="page-title">未能启动新手团</h1>
          <p className="mb-6" style={{ color: 'var(--color-text-secondary)' }}>
            请检查本地服务状态后重试，已创建的示例内容不会重复生成。
          </p>
          <button className="btn-primary flex items-center gap-2" onClick={() => void run()}>
            <RefreshCw className="h-4 w-4" aria-hidden="true" />
            重试
          </button>
        </>
      )}
    </main>
  )
}
