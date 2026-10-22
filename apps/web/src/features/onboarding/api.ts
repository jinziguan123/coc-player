import { api, localApi } from '@/api/client'

export interface AIStatus {
  configured: boolean
  name: string | null
}

export interface OnboardingStartResult {
  session_id: string
  status: string
  reused: boolean
}

export function checkAIStatus() {
  return api.get<AIStatus>('/settings/ai/status')
}

// 同一时刻只发一个建局请求：开发态 StrictMode 会把页面的自动启动跑两遍，
// 两个并发 POST 曾各建出一套模组/角色/会话（后端另有锁兜底）。
let inflight: Promise<OnboardingStartResult> | null = null

export function startOnboarding() {
  if (!inflight) {
    inflight = localApi.post<OnboardingStartResult>('/onboarding/start')
      .finally(() => { inflight = null })
  }
  return inflight
}
